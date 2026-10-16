import json
import os
from http import HTTPStatus

import boto3
import psycopg2
from psycopg2 import errors

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
MINECRAFT_PORT = 25565


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


# lee los servidores del usuario de la tabla servers que crea create_server
def get_user_servers(user_id):
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT instance_id, server_name, status, created_at FROM servers "
                "WHERE user_id = %s ORDER BY created_at DESC;",
                (user_id,),
            )
            return cur.fetchall()
    except errors.UndefinedTable:
        # todavia no se ha creado ningun servidor
        return []
    finally:
        conn.close()


# estado e IP actuales desde EC2; la IP publica cambia cada vez que se enciende la instancia
def get_live_info(instance_ids):
    if not instance_ids:
        return {}
    # con Filters no falla si alguna instancia ya no existe, a diferencia de InstanceIds
    reservations = ec2_client.describe_instances(
        Filters=[{"Name": "instance-id", "Values": instance_ids}]
    )["Reservations"]
    info = {}
    for reservation in reservations:
        for instance in reservation["Instances"]:
            info[instance["InstanceId"]] = {
                "state": instance["State"]["Name"],
                "publicIp": instance.get("PublicIpAddress"),
                "instanceType": instance.get("InstanceType"),
            }
    return info


def lambda_handler(event, context):
    try:
        try:
            payload = get_payload(event)
        except json.JSONDecodeError:
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        user_id = str(payload.get("userId", "")).strip()
        if not user_id or len(user_id) > 100:
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "El campo userId es requerido"}
            )

        rows = get_user_servers(user_id)
        live = get_live_info([row[0] for row in rows])

        servers = []
        for instance_id, server_name, db_status, created_at in rows:
            info = live.get(instance_id)
            # si la instancia ya no existe en EC2 se reporta como terminada
            status = info["state"] if info else "terminated"
            public_ip = info["publicIp"] if info else None
            servers.append(
                {
                    "serverId": instance_id,
                    "serverName": server_name,
                    "status": status,
                    "dbStatus": db_status,
                    "publicIp": public_ip,
                    "address": f"{public_ip}:{MINECRAFT_PORT}" if public_ip else None,
                    "instanceType": info["instanceType"] if info else None,
                    "createdAt": created_at.isoformat() if created_at else None,
                }
            )

        return response(HTTPStatus.OK, {"userId": user_id, "servers": servers})

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
