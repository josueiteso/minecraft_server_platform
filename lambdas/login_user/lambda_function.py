import base64
import hashlib
import hmac
import json
import os
import time
from http import HTTPStatus

import boto3
import psycopg2
from psycopg2 import errors

REGION = "us-east-1"
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")

# este es el secreto con el que se firman los tokens
JWT_SECRET_ID = os.environ.get("JWT_SECRET_ID", "minecraft-jwt-secret")
TOKEN_TTL_SECONDS = 12 * 60 * 60
jwt_secret = None

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


# se lee una vez por contenedor de lambda, no en cada peticion
def get_jwt_secret():
    global jwt_secret
    if jwt_secret is None:
        jwt_secret = secrets_client.get_secret_value(SecretId=JWT_SECRET_ID)[
            "SecretString"
        ]
    return jwt_secret


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


# token JWT con el id del usuario en "sub"
def create_token(user_id, username):
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "sub": str(user_id),
        "username": username,
        "iat": now,
        "exp": now + TOKEN_TTL_SECONDS,
    }
    signing_input = ".".join(
        b64url(json.dumps(part, separators=(",", ":")).encode())
        for part in (header, claims)
    )
    signature = hmac.new(
        get_jwt_secret().encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{b64url(signature)}"


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
                "token": create_token(user_id, username),
            },
        )

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
