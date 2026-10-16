import hashlib
import hmac
import json
import os
from http import HTTPStatus

import boto3
import psycopg2
from psycopg2 import errors

REGION = "us-east-1"
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")

# mismo mensaje para email inexistente o password incorrecta, asi no se revela que cuentas existen
INVALID_CREDENTIALS = "Email o password incorrectos"


def response(status, body):
    return {
        "statusCode": status.value,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# acepta el evento de aws lambda invoke o el body de API Gateway (proxy integration)
def get_payload(event):
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


# compara contra el formato que guarda register_user: algoritmo$iteraciones$salt$hash
def verify_password(password, stored_hash):
    try:
        algorithm, iterations, salt, expected = stored_hash.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
    ).hex()
    return hmac.compare_digest(digest, expected)


def find_user(email):
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash FROM users WHERE email = %s;",
                (email,),
            )
            return cur.fetchone()
    except errors.UndefinedTable:
        # todavia no se ha registrado nadie
        return None
    finally:
        conn.close()


def lambda_handler(event, context):
    try:
        try:
            payload = get_payload(event)
        except json.JSONDecodeError:
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))

        if not email or not password:
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "email y password son requeridos"}
            )

        user = find_user(email)
        if user is None or not verify_password(password, user[2]):
            print("Intento de inicio de sesion fallido")
            return response(HTTPStatus.UNAUTHORIZED, {"error": INVALID_CREDENTIALS})

        user_id, username, _ = user
        print(f"Inicio de sesion: {user_id}")
        return response(
            HTTPStatus.OK,
            {
                "message": "Inicio de sesion correcto",
                "userId": str(user_id),
                "username": username,
            },
        )

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
