import base64
import hashlib
import hmac
import json
import os
import re
import time
from contextlib import contextmanager
from http import HTTPStatus

import boto3
import psycopg2
from botocore.config import Config
from botocore.exceptions import ClientError
from psycopg2 import errors

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)
cloudwatch_client = boto3.client(
    "cloudwatch",
    region_name=REGION,
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "MinecraftPlatform")
INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8,17}")
# secreto con el que login_user firma los tokens
JWT_SECRET_ID = os.environ.get("JWT_SECRET_ID", "minecraft-jwt-secret")


# instrumentacion de metricas
class Metrics:
    def __init__(self):
        self.data = []

    def _add(self, name, value, unit, dims):
        self.data.append({"MetricName": name, "Value": value, "Unit": unit})
        if dims:
            self.data.append(
                {
                    "MetricName": name,
                    "Value": value,
                    "Unit": unit,
                    "Dimensions": [
                        {"Name": k, "Value": str(v)} for k, v in dims.items()
                    ],
                }
            )

    def count(self, name, value=1, **dims):
        self._add(name, value, "Count", dims)

    def timing(self, name, milliseconds):
        self._add(name, milliseconds, "Milliseconds", {})

    @contextmanager
    def timer(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.timing(name, (time.perf_counter() - start) * 1000)

    def flush(self):
        try:
            # put_metric_data acepta hasta 1000 datos por llamada
            for i in range(0, len(self.data), 1000):
                cloudwatch_client.put_metric_data(
                    Namespace=METRICS_NAMESPACE, MetricData=self.data[i : i + 1000]
                )
        except Exception as err:  # noqa: BLE001
            print(f"No se pudieron publicar metricas: {type(err).__name__}: {err}")


class AuthError(Exception):
    """Token ausente, mal formado, con firma incorrecta o vencido."""


_jwt_secret = None


# se lee una vez por contenedor de lambda, no en cada peticion
def get_jwt_secret():
    global _jwt_secret
    if _jwt_secret is None:
        _jwt_secret = secrets_client.get_secret_value(SecretId=JWT_SECRET_ID)[
            "SecretString"
        ]
    return _jwt_secret


def b64url_decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# saca el token del header "Authorization: Bearer <token>"
def get_bearer_token(event):
    headers = event.get("headers") or {}
    for name, value in headers.items():
        if name.lower() == "authorization":
            scheme, _, token = str(value).strip().partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    raise AuthError("Falta el token de autenticacion")


# valida el token que emitio login_user y regresa el id del usuario (claim "sub")
def get_authenticated_user_id(event):
    parts = get_bearer_token(event).split(".")
    if len(parts) != 3:
        raise AuthError("Token mal formado")
    header_b64, claims_b64, signature_b64 = parts

    try:
        header = json.loads(b64url_decode(header_b64))
        # solo se acepta HS256, nunca "none" ni otro algoritmo
        if not isinstance(header, dict) or header.get("alg") != "HS256":
            raise AuthError("Algoritmo no soportado")

        expected = hmac.new(
            get_jwt_secret().encode(),
            f"{header_b64}.{claims_b64}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, b64url_decode(signature_b64)):
            raise AuthError("Firma invalida")

        claims = json.loads(b64url_decode(claims_b64))
    except ValueError as err:  # base64 o JSON invalido
        raise AuthError("Token mal formado") from err

    if not isinstance(claims, dict):
        raise AuthError("Token mal formado")
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= time.time():
        raise AuthError("Token vencido")
    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise AuthError("Token sin usuario")
    return user_id


def response(status, body):
    return {
        "statusCode": status.value,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# acepta el evento de aws lambda invoke o API Gateway (query string o body)
def get_payload(event):
    if isinstance(event.get("queryStringParameters"), dict):
        return event["queryStringParameters"]
    if isinstance(event.get("body"), str):
        return json.loads(event["body"] or "{}")
    return event


# carga las credenciales de RDS desde secrets manager (mismo patron que create_server)
def get_rds_credentials():
    instance = rds_client.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)[
        "DBInstances"
    ][0]
    secret = json.loads(
        secrets_client.get_secret_value(
            SecretId=instance["MasterUserSecret"]["SecretArn"]
        )["SecretString"]
    )
    return {
        "host": instance["Endpoint"]["Address"],
        "port": instance["Endpoint"]["Port"],
        "username": secret["username"],
        "password": secret["password"],
        "dbname": instance["DBName"],
    }


def connect_db():
    creds = get_rds_credentials()
    return psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        database=creds["dbname"],
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )


# termina la instancia; si ya no existe en EC2 no es error, solo falta quitarla de la tabla
# devuelve True si la instancia ya no existia
def terminate_instance(instance_id):
    try:
        ec2_client.terminate_instances(InstanceIds=[instance_id])
        return False
    except ClientError as err:
        if err.response["Error"]["Code"] != "InvalidInstanceID.NotFound":
            raise
        return True


def handle_request(event, metrics):
    # etapa en curso; si algo falla, el except final sabe en que paso ocurrio
    stage = "request"
    try:
        # el dueno es siempre el usuario del token, nunca un dato de la peticion
        try:
            user_id = get_authenticated_user_id(event)
        except AuthError as err:
            print(f"Peticion no autenticada: {err}")
            return response(
                HTTPStatus.UNAUTHORIZED, {"error": "Sesion invalida o expirada"}
            )

        try:
            payload = get_payload(event)
        except json.JSONDecodeError:
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        server_id = str(payload.get("serverId", "")).strip()

        if not INSTANCE_ID_RE.fullmatch(server_id):
            return response(HTTPStatus.BAD_REQUEST, {"error": "serverId invalido"})

        stage = "connect"
        with metrics.timer("DbConnectLatency"):
            conn = connect_db()
        try:
            with conn.cursor() as cur:
                # solo el dueno puede borrar su servidor
                stage = "lookup"
                try:
                    cur.execute(
                        "SELECT 1 FROM servers WHERE user_id = %s AND instance_id = %s;",
                        (user_id, server_id),
                    )
                    existe = cur.fetchone() is not None
                except errors.UndefinedTable:
                    existe = False
                if not existe:
                    return response(
                        HTTPStatus.NOT_FOUND, {"error": "Servidor no encontrado"}
                    )

                # primero EC2: si falla, el registro se queda y se puede reintentar
                stage = "terminate"
                try:
                    with metrics.timer("TerminateLatency"):
                        already_gone = terminate_instance(server_id)
                except ClientError as err:
                    # ej. UnauthorizedOperation, RequestLimitExceeded
                    metrics.count(
                        "TerminateFailures", ErrorCode=err.response["Error"]["Code"]
                    )
                    raise
                if already_gone:
                    # la instancia desaparecio fuera de la plataforma (consola, rollback, etc.)
                    metrics.count("InstanceAlreadyGone")

                stage = "delete_row"
                cur.execute(
                    "DELETE FROM servers WHERE user_id = %s AND instance_id = %s;",
                    (user_id, server_id),
                )
            conn.commit()
        finally:
            conn.close()

        print(f"Servidor {server_id} del usuario {user_id} eliminado")
        return response(
            HTTPStatus.OK, {"message": "Servidor eliminado", "serverId": server_id}
        )

    except Exception as e:  # noqa: BLE001
        if stage in ("connect", "lookup", "delete_row"):
            metrics.count("DeleteDbFailures", Stage=stage)
        if stage == "delete_row":
            # EC2 ya fue terminada pero la fila sigue en la tabla. El usuario seguira viendo un servidor que no existe hasta que reintente
            metrics.count("DeleteInconsistentState")
        print(f"Error procesando solicitud (etapa {stage}): {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )


def lambda_handler(event, context):
    metrics = Metrics()
    started = time.perf_counter()
    metrics.count("DeleteRequests")
    result = None
    try:
        result = handle_request(event, metrics)
        return result
    finally:
        status = result["statusCode"] if result else 500
        if status < 300:
            metrics.count("ServersDeleted")
        elif status < 500:
            metrics.count("DeleteClientErrors")
            if status == 401:
                metrics.count("DeleteAuthFailures")
            elif status == 400:
                metrics.count("DeleteValidationFailures")
            elif status == 404:
                metrics.count("DeleteNotFound")
        else:
            metrics.count("DeleteServerErrors")
        metrics.timing("DeleteLatency", (time.perf_counter() - started) * 1000)
        metrics.flush()
