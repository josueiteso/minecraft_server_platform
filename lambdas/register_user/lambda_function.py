import hashlib
import json
import os
import re
import secrets
from http import HTTPStatus

import boto3
import psycopg2
from psycopg2 import errors

REGION = "us-east-1"
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 8
HASH_ITERATIONS = 600_000


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


def ensure_users_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(30) UNIQUE NOT NULL,
            email VARCHAR(255) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)


# la contrasena nunca se guarda en texto plano: se guarda algoritmo$iteraciones$salt$hash
def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), HASH_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${digest}"


def validate(username, email, password):
    if not USERNAME_RE.fullmatch(username):
        return "username debe tener de 3 a 30 caracteres (letras, numeros o _)"
    if not EMAIL_RE.fullmatch(email) or len(email) > 255:
        return "email invalido"
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"password debe tener al menos {MIN_PASSWORD_LENGTH} caracteres"
    return None


def lambda_handler(event, context):
    try:
        try:
            payload = get_payload(event)
        except json.JSONDecodeError:
            return response(HTTPStatus.BAD_REQUEST, {"error": "JSON invalido"})

        username = str(payload.get("username", "")).strip()
        email = str(payload.get("email", "")).strip().lower()
        password = str(payload.get("password", ""))

        error = validate(username, email, password)
        if error:
            return response(HTTPStatus.BAD_REQUEST, {"error": error})

        conn = connect_db()
        try:
            with conn.cursor() as cur:
                ensure_users_table(cur)
                cur.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id;",
                    (username, email, hash_password(password)),
                )
                user_id = cur.fetchone()[0]
            conn.commit()
        except errors.UniqueViolation:
            conn.rollback()
            return response(
                HTTPStatus.CONFLICT, {"error": "El username o el email ya existen"}
            )
        finally:
            conn.close()

        print(f"Usuario registrado: {user_id}")
        return response(
            HTTPStatus.CREATED,
            {
                "message": "Usuario registrado",
                "userId": str(user_id),
                "username": username,
            },
        )

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
