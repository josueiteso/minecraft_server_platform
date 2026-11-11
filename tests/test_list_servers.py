from datetime import datetime
from unittest.mock import MagicMock

import pytest
from conftest import cargar_lambda, leer_respuesta
from psycopg2 import errors

listar = cargar_lambda("list_servers")

CREADO = datetime(2026, 11, 10, 18, 30)


def evento_get(user_id):
    # GET /servers?userId=... como lo manda API Gateway
    return {"queryStringParameters": {"userId": user_id}}


@pytest.fixture
def conectar(monkeypatch, db_falsa):
    conn, _ = db_falsa
    monkeypatch.setattr(listar, "connect_db", lambda: conn)
    return db_falsa


@pytest.fixture
def ec2(monkeypatch):
    cliente = MagicMock()
    monkeypatch.setattr(listar, "ec2_client", cliente)
    return cliente


def instancia(instance_id, estado, ip=None):
    datos = {
        "InstanceId": instance_id,
        "State": {"Name": estado},
        "InstanceType": "t3.medium",
    }
    if ip:
        datos["PublicIpAddress"] = ip
    return datos


def test_sin_user_id_regresa_400():
    status, body = leer_respuesta(listar.lambda_handler({}, None))
    assert status == 400
    assert body["error"] == "El campo userId es requerido"


def test_usuario_sin_servidores_regresa_lista_vacia(conectar, ec2):
    _, cursor = conectar
    cursor.fetchall.return_value = []

    status, body = leer_respuesta(listar.lambda_handler(evento_get("1"), None))

    assert status == 200
    assert body == {"userId": "1", "servers": []}
    ec2.describe_instances.assert_not_called()


def test_sin_tabla_servers_regresa_lista_vacia(conectar, ec2):
    _, cursor = conectar
    cursor.execute.side_effect = errors.UndefinedTable()

    status, body = leer_respuesta(listar.lambda_handler(evento_get("1"), None))

    assert status == 200
    assert body["servers"] == []


def test_regresa_estado_e_ip_desde_ec2(conectar, ec2):
    _, cursor = conectar
    cursor.fetchall.return_value = [
        ("i-0aaa", "Survival con amigos", "creating", CREADO),
        ("i-0bbb", "Mundo creativo", "stopped", CREADO),
    ]
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [instancia("i-0aaa", "running", "54.210.33.12")]},
            {"Instances": [instancia("i-0bbb", "stopped")]},
        ]
    }

    status, body = leer_respuesta(listar.lambda_handler(evento_get("1"), None))

    assert status == 200
    encendido, apagado = body["servers"]
    assert encendido["serverId"] == "i-0aaa"
    assert encendido["status"] == "running"
    assert encendido["dbStatus"] == "creating"
    assert encendido["publicIp"] == "54.210.33.12"
    assert encendido["address"] == "54.210.33.12:25565"
    assert encendido["instanceType"] == "t3.medium"
    assert encendido["createdAt"] == "2026-11-10T18:30:00"
    assert apagado["status"] == "stopped"
    assert apagado["publicIp"] is None
    assert apagado["address"] is None


def test_instancia_que_ya_no_existe_aparece_como_terminated(conectar, ec2):
    _, cursor = conectar
    cursor.fetchall.return_value = [("i-0ccc", "Mundo borrado", "running", CREADO)]
    ec2.describe_instances.return_value = {"Reservations": []}

    status, body = leer_respuesta(listar.lambda_handler(evento_get("1"), None))

    servidor = body["servers"][0]
    assert status == 200
    assert servidor["status"] == "terminated"
    assert servidor["publicIp"] is None


def test_solo_consulta_los_servidores_del_usuario(conectar, ec2):
    _, cursor = conectar
    cursor.fetchall.return_value = []

    listar.lambda_handler(evento_get("42"), None)

    assert cursor.execute.call_args.args[1] == ("42",)


def test_error_de_ec2_regresa_500(conectar, ec2):
    _, cursor = conectar
    cursor.fetchall.return_value = [("i-0aaa", "Survival", "running", CREADO)]
    ec2.describe_instances.side_effect = RuntimeError("EC2 no disponible")

    status, body = leer_respuesta(listar.lambda_handler(evento_get("1"), None))

    assert status == 500
    assert body["error"] == "Internal Server Error"
