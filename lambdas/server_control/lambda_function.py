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

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)
ssm_client = boto3.client("ssm", region_name=REGION)
cloudwatch_client = boto3.client(
    "cloudwatch",
    region_name=REGION,
    config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}),
)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "minecraft-backups-CAMBIAR")
METRICS_NAMESPACE = os.environ.get("METRICS_NAMESPACE", "MinecraftPlatform")
# secreto con el que login_user firma los tokens
JWT_SECRET_ID = os.environ.get("JWT_SECRET_ID", "minecraft-jwt-secret")

# el respaldo (backup) queda fuera por ahora: las EC2 no tienen rol de IAM ni AWS CLI para
# subir el mundo a S3. Sus funciones se conservan abajo para cuando se agregue
VALID_ACTIONS = {"start", "stop", "status"}
INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
WORLD_DIR = "/minecraft"
SSM_POLL_SECONDS = 5
SSM_TIMEOUT_SECONDS = 150


# instrumentacion de metricas (mismo patron que create_server y delete_server)
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


# acepta el evento de aws lambda invoke o API Gateway (body JSON)
def get_payload(event):
    if isinstance(event.get("body"), str):
        payload = json.loads(event["body"] or "{}")
        if not isinstance(payload, dict):
            raise TypeError("El body debe ser un objeto JSON")
        return payload
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


def ensure_backups_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backups (
            id SERIAL PRIMARY KEY,
            instance_id VARCHAR(50) NOT NULL,
            s3_key VARCHAR(300) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)


# el usuario solo puede operar sobre servidores que le pertenecen
def owns_server(cur, user_id, instance_id):
    cur.execute(
        "SELECT 1 FROM servers WHERE instance_id = %s AND user_id = %s;",
        (instance_id, user_id),
    )
    return cur.fetchone() is not None


def set_server_status(cur, instance_id, status):
    cur.execute(
        "UPDATE servers SET status = %s WHERE instance_id = %s;",
        (status, instance_id),
    )


def record_backup(cur, instance_id, s3_key):
    ensure_backups_table(cur)
    cur.execute(
        "INSERT INTO backups (instance_id, s3_key) VALUES (%s, %s);",
        (instance_id, s3_key),
    )


# estado actual en EC2; si la instancia ya no existe se reporta como "terminated"
def get_instance_state(instance_id):
    try:
        reservations = ec2_client.describe_instances(InstanceIds=[instance_id])[
            "Reservations"
        ]
    except ClientError as err:
        if err.response["Error"]["Code"] == "InvalidInstanceID.NotFound":
            return "terminated"
        raise
    if not reservations:
        return "terminated"
    return reservations[0]["Instances"][0]["State"]["Name"]


def build_backup_script(bucket, s3_key):
    # el servidor se detiene para que el mundo quede consistente; al final siempre se vuelve a levantar
    return f"""#!/bin/bash
set -e
TMP_FILE=$(mktemp /tmp/world-XXXXXX.tar.gz)
trap 'rm -f "$TMP_FILE"; systemctl start minecraft || true' EXIT
systemctl stop minecraft
tar -czf "$TMP_FILE" -C {WORLD_DIR} world
aws s3 cp "$TMP_FILE" "s3://{bucket}/{s3_key}" --only-show-errors
"""


# ejecuta el respaldo dentro de la EC2 usando SSM Run Command y espera el resultado
def run_backup_on_instance(instance_id, s3_key):
    script = build_backup_script(BACKUP_BUCKET, s3_key)
    sent = ssm_client.send_command(
        InstanceIds=[instance_id],
        DocumentName="AWS-RunShellScript",
        Parameters={"commands": [script]},
        TimeoutSeconds=SSM_TIMEOUT_SECONDS,
    )
    command_id = sent["Command"]["CommandId"]
    deadline = time.time() + SSM_TIMEOUT_SECONDS
    while time.time() < deadline:
        time.sleep(SSM_POLL_SECONDS)
        try:
            invocation = ssm_client.get_command_invocation(
                CommandId=command_id, InstanceId=instance_id
            )
        except ssm_client.exceptions.InvocationDoesNotExist:
            continue
        status = invocation["Status"]
        if status == "Success":
            return
        if status in ("Cancelled", "TimedOut", "Failed", "Cancelling"):
            raise RuntimeError(
                f"Respaldo fallido en {instance_id}: {status} - {invocation.get('StatusDetails')}"
            )
    raise TimeoutError(f"El respaldo de {instance_id} no termino a tiempo")


# llama a EC2 (start_instances o stop_instances) midiendo el tiempo y contando las fallas
def call_ec2(metrics, action, method, instance_id):
    try:
        with metrics.timer("Ec2ControlLatency"):
            method(InstanceIds=[instance_id])
    except ClientError as err:
        # ej. IncorrectInstanceState, InsufficientInstanceCapacity, UnauthorizedOperation
        metrics.count(
            "Ec2ControlFailures",
            Action=action,
            ErrorCode=err.response["Error"]["Code"],
        )
        raise


def do_start(cur, instance_id, metrics):
    state = get_instance_state(instance_id)
    if state in ("running", "pending"):
        return HTTPStatus.OK, {
            "message": "El servidor ya esta encendido",
            "status": state,
        }
    if state != "stopped":
        return HTTPStatus.CONFLICT, {"error": f"El servidor esta en estado '{state}'"}

    call_ec2(metrics, "start", ec2_client.start_instances, instance_id)
    set_server_status(cur, instance_id, "starting")
    metrics.count("ServersStarted")
    return HTTPStatus.OK, {"message": "Servidor encendiendose", "status": "starting"}


# apaga sin respaldar: el mundo queda guardado en el disco de la EC2 al apagarse
def do_stop(cur, instance_id, metrics):
    state = get_instance_state(instance_id)
    if state in ("stopped", "stopping"):
        return HTTPStatus.OK, {
            "message": "El servidor ya esta apagado",
            "status": state,
        }
    if state != "running":
        return HTTPStatus.CONFLICT, {"error": f"El servidor esta en estado '{state}'"}

    call_ec2(metrics, "stop", ec2_client.stop_instances, instance_id)
    set_server_status(cur, instance_id, "stopping")
    metrics.count("ServersStopped")
    return HTTPStatus.OK, {"message": "Servidor apagandose", "status": "stopping"}


def do_status(cur, instance_id):
    state = get_instance_state(instance_id)
    set_server_status(cur, instance_id, state)
    return HTTPStatus.OK, {"instanceId": instance_id, "status": state}


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
        except (ValueError, TypeError):  # JSONDecodeError hereda de ValueError
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        action = str(payload.get("action", "")).lower()
        instance_id = str(payload.get("serverId", "")).strip()

        if action not in VALID_ACTIONS:
            return response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"action debe ser uno de {sorted(VALID_ACTIONS)}"},
            )
        if not INSTANCE_ID_RE.fullmatch(instance_id):
            return response(HTTPStatus.BAD_REQUEST, {"error": "serverId invalido"})

        stage = "connect"
        with metrics.timer("ControlDbConnectLatency"):
            conn = connect_db()
        try:
            with conn.cursor() as cur:
                stage = "lookup"
                if not owns_server(cur, user_id, instance_id):
                    return response(
                        HTTPStatus.NOT_FOUND, {"error": "Servidor no encontrado"}
                    )

                stage = action
                if action == "start":
                    code, body = do_start(cur, instance_id, metrics)
                elif action == "stop":
                    code, body = do_stop(cur, instance_id, metrics)
                else:
                    code, body = do_status(cur, instance_id)
            conn.commit()
        finally:
            conn.close()

        return response(code, body)

    except Exception as e:  # noqa: BLE001
        if stage in ("connect", "lookup"):
            metrics.count("ControlDbFailures", Stage=stage)
        print(f"Error procesando solicitud (etapa {stage}): {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )


def lambda_handler(event, context):
    metrics = Metrics()
    started = time.perf_counter()
    metrics.count("ControlRequests")
    result = None
    try:
        result = handle_request(event, metrics)
        return result
    finally:
        status = result["statusCode"] if result else 500
        if status < 300:
            metrics.count("ControlSuccess")
        elif status < 500:
            metrics.count("ControlClientErrors")
            if status == 401:
                metrics.count("ControlAuthFailures")
            elif status == 400:
                metrics.count("ControlValidationFailures")
            elif status == 404:
                metrics.count("ControlNotFound")
            elif status == 409:
                metrics.count("ControlConflicts")
        else:
            metrics.count("ControlServerErrors")
        metrics.timing("ControlLatency", (time.perf_counter() - started) * 1000)
        metrics.flush()
