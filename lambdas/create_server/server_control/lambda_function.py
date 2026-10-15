import json
import os
import re
import time
from http import HTTPStatus

import boto3
import psycopg2

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)
ssm_client = boto3.client("ssm", region_name=REGION)
cloudwatch_client = boto3.client("cloudwatch", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
BACKUP_BUCKET = os.environ.get("BACKUP_BUCKET", "minecraft-backups-CAMBIAR")
METRIC_NAMESPACE = "MinecraftPlatform"

VALID_ACTIONS = {"start", "stop", "backup", "status"}
INSTANCE_ID_RE = re.compile(r"^i-[0-9a-f]{8,17}$")
WORLD_DIR = "/minecraft"
SSM_POLL_SECONDS = 5
SSM_TIMEOUT_SECONDS = 150


def response(status, body):
    return {"statusCode": status.value, "body": json.dumps(body)}


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


def get_instance_state(instance_id):
    reservations = ec2_client.describe_instances(InstanceIds=[instance_id])[
        "Reservations"
    ]
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


def publish_metric(name):
    cloudwatch_client.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[{"MetricName": name, "Value": 1, "Unit": "Count"}],
    )


def do_start(cur, instance_id):
    ec2_client.start_instances(InstanceIds=[instance_id])
    set_server_status(cur, instance_id, "starting")
    return HTTPStatus.OK, {"message": "Servidor encendiendose", "status": "starting"}


def do_stop(cur, instance_id, user_id):
    state = get_instance_state(instance_id)
    if state == "stopped":
        set_server_status(cur, instance_id, "stopped")
        return HTTPStatus.OK, {"message": "El servidor ya estaba apagado", "status": "stopped"}
    if state != "running":
        return HTTPStatus.CONFLICT, {"error": f"El servidor esta en estado '{state}'"}

    s3_key = f"backups/{user_id}/{instance_id}/{int(time.time())}.tar.gz"
    try:
        run_backup_on_instance(instance_id, s3_key)
    except Exception as backup_err:  # noqa: BLE001
        # no se apaga la instancia si el respaldo falla, para no perder el mundo
        print(f"Fallo el respaldo, la instancia sigue encendida: {backup_err}")
        publish_metric("BackupsFailed")
        set_server_status(cur, instance_id, "running")
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No se pudo respaldar el mundo"}

    publish_metric("BackupsCompleted")
    record_backup(cur, instance_id, s3_key)
    ec2_client.stop_instances(InstanceIds=[instance_id])
    set_server_status(cur, instance_id, "stopping")
    return HTTPStatus.OK, {
        "message": "Respaldo guardado y servidor apagandose",
        "status": "stopping",
        "backupKey": s3_key,
    }


def do_backup(cur, instance_id, user_id):
    state = get_instance_state(instance_id)
    if state != "running":
        return HTTPStatus.CONFLICT, {"error": "Solo se puede respaldar un servidor encendido"}

    s3_key = f"backups/{user_id}/{instance_id}/{int(time.time())}.tar.gz"
    try:
        run_backup_on_instance(instance_id, s3_key)
    except Exception as backup_err:  # noqa: BLE001
        print(f"Fallo el respaldo: {backup_err}")
        publish_metric("BackupsFailed")
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "No se pudo respaldar el mundo"}

    publish_metric("BackupsCompleted")
    record_backup(cur, instance_id, s3_key)
    return HTTPStatus.OK, {"message": "Respaldo guardado", "backupKey": s3_key}


def do_status(cur, instance_id):
    state = get_instance_state(instance_id)
    set_server_status(cur, instance_id, state)
    return HTTPStatus.OK, {"instanceId": instance_id, "status": state}


def lambda_handler(event, context):
    try:
        action = str(event.get("action", "")).lower()
        user_id = event.get("userId")
        instance_id = str(event.get("serverId", ""))

        if not user_id:
            return response(HTTPStatus.BAD_REQUEST, {"error": "El campo userId es requerido"})
        if action not in VALID_ACTIONS:
            return response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"action debe ser uno de {sorted(VALID_ACTIONS)}"},
            )
        if not INSTANCE_ID_RE.fullmatch(instance_id):
            return response(HTTPStatus.BAD_REQUEST, {"error": "serverId invalido"})

        conn = connect_db()
        try:
            with conn.cursor() as cur:
                if not owns_server(cur, user_id, instance_id):
                    return response(HTTPStatus.NOT_FOUND, {"error": "Servidor no encontrado"})

                if action == "start":
                    code, body = do_start(cur, instance_id)
                elif action == "stop":
                    code, body = do_stop(cur, instance_id, user_id)
                elif action == "backup":
                    code, body = do_backup(cur, instance_id, user_id)
                else:
                    code, body = do_status(cur, instance_id)
            conn.commit()
        finally:
            conn.close()

        return response(code, body)

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"})