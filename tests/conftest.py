import base64
import hashlib
import hmac
import importlib.util
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

RAIZ = Path(__file__).resolve().parent.parent

# las lambdas crean clientes de boto3 al importarse; solo necesitan una region, no credenciales
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


# todas las lambdas se llaman lambda_function.py, asi que se cargan con un nombre distinto cada una
def cargar_lambda(nombre):
    ruta = RAIZ / "lambdas" / nombre / "lambda_function.py"
    spec = importlib.util.spec_from_file_location(f"{nombre}_lambda", ruta)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


SECRETO_PRUEBA = "secreto-test"


def _b64(datos):
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


# firma un token como lo haria login_user pero de forma independiente para poder probar tokens invalidos
def firmar_token(user_id, secreto=SECRETO_PRUEBA, vence_en=3600, alg="HS256"):
    ahora = int(time.time())
    header = _b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    claims = _b64(
        json.dumps(
            {"sub": str(user_id), "iat": ahora, "exp": ahora + vence_en}
        ).encode()
    )
    firma = hmac.new(
        secreto.encode(), f"{header}.{claims}".encode(), hashlib.sha256
    ).digest()
    return f"{header}.{claims}.{_b64(firma)}"


# evento como lo manda API Gateway (HTTP API, payload 2.0)
# con token manda Authorization Bearer
def evento_api(body, token=None):
    evento = {"body": json.dumps(body)}
    if token:
        evento["headers"] = {"authorization": f"Bearer {token}"}
    return evento


def leer_respuesta(resultado):
    return resultado["statusCode"], json.loads(resultado["body"])


# conexion falsa a RDS: conn.cursor() funciona como "with" y regresa un cursor falso
@pytest.fixture
def db_falsa():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn, cursor
