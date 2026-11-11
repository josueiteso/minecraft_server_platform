import importlib.util
import json
import os
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


# evento como lo manda API Gateway (HTTP API, payload 2.0)
def evento_api(body):
    return {"body": json.dumps(body)}


def leer_respuesta(resultado):
    return resultado["statusCode"], json.loads(resultado["body"])


# conexion falsa a RDS: conn.cursor() funciona como "with" y regresa un cursor falso
@pytest.fixture
def db_falsa():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn, cursor
