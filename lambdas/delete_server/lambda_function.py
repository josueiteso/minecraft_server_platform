import json
import os
import re
from http import HTTPStatus

import boto3
import psycopg2
from botocore.exceptions import ClientError
from psycopg2 import errors

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
INSTANCE_ID_RE = re.compile(r"i-[0-9a-f]{8,17}")


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
def terminate_instance(instance_id):
    try:
        ec2_client.terminate_instances(InstanceIds=[instance_id])
    except ClientError as err:
        if err.response["Error"]["Code"] != "InvalidInstanceID.NotFound":
            raise


def lambda_handler(event, context):
    try:
        try:
            payload = get_payload(event)
        except json.JSONDecodeError:
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        user_id = str(payload.get("userId", "")).strip()
        server_id = str(payload.get("serverId", "")).strip()

        if not user_id or len(user_id) > 100:
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "El campo userId es requerido"}
            )
        if not INSTANCE_ID_RE.fullmatch(server_id):
            return response(HTTPStatus.BAD_REQUEST, {"error": "serverId invalido"})

        conn = connect_db()
        try:
            with conn.cursor() as cur:
                # solo el dueno puede borrar su servidor
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
                terminate_instance(server_id)
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
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
