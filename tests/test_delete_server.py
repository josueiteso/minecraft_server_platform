from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from conftest import cargar_lambda, leer_respuesta
from psycopg2 import errors

borrar = cargar_lambda("delete_server")

SERVER_ID = "i-0309ed93dd6aec9af"


def evento_delete(user_id, server_id):
    # DELETE /servers?userId=...&serverId=... como lo manda API Gateway
    return {"queryStringParameters": {"userId": user_id, "serverId": server_id}}


@pytest.fixture
def conectar(monkeypatch, db_falsa):
    conn, _ = db_falsa
    monkeypatch.setattr(borrar, "connect_db", lambda: conn)
    return db_falsa


@pytest.fixture
def ec2(monkeypatch):
    cliente = MagicMock()
    monkeypatch.setattr(borrar, "ec2_client", cliente)
    return cliente


def error_ec2(codigo):
    return ClientError(
        {"Error": {"Code": codigo, "Message": codigo}}, "TerminateInstances"
    )


@pytest.mark.parametrize(
    "user_id, server_id, mensaje",
    [
        ("", SERVER_ID, "El campo userId es requerido"),
        ("1", "", "serverId invalido"),
        ("1", "i-123; DROP TABLE servers", "serverId invalido"),
    ],
)
def test_datos_invalidos_regresa_400(user_id, server_id, mensaje):
    status, body = leer_respuesta(
        borrar.lambda_handler(evento_delete(user_id, server_id), None)
    )
    assert status == 400
    assert body["error"] == mensaje


def test_borra_la_instancia_y_el_registro(conectar, ec2):
    conn, cursor = conectar
    cursor.fetchone.return_value = (1,)

    status, body = leer_respuesta(
        borrar.lambda_handler(evento_delete("1", SERVER_ID), None)
    )

    assert status == 200
    assert body["serverId"] == SERVER_ID
    ec2.terminate_instances.assert_called_once_with(InstanceIds=[SERVER_ID])
    consulta, valores = cursor.execute.call_args.args
    assert consulta.startswith("DELETE FROM servers")
    assert valores == ("1", SERVER_ID)
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_servidor_de_otro_usuario_regresa_404(conectar, ec2):
    conn, cursor = conectar
    cursor.fetchone.return_value = None

    status, body = leer_respuesta(
        borrar.lambda_handler(evento_delete("2", SERVER_ID), None)
    )

    assert status == 404
    assert body["error"] == "Servidor no encontrado"
    ec2.terminate_instances.assert_not_called()
    conn.commit.assert_not_called()


def test_sin_tabla_servers_regresa_404(conectar, ec2):
    _, cursor = conectar
    cursor.execute.side_effect = errors.UndefinedTable()

    status, _ = leer_respuesta(
        borrar.lambda_handler(evento_delete("1", SERVER_ID), None)
    )

    assert status == 404
    ec2.terminate_instances.assert_not_called()


def test_instancia_que_ya_no_existe_se_quita_de_la_lista(conectar, ec2):
    conn, cursor = conectar
    cursor.fetchone.return_value = (1,)
    ec2.terminate_instances.side_effect = error_ec2("InvalidInstanceID.NotFound")

    status, _ = leer_respuesta(
        borrar.lambda_handler(evento_delete("1", SERVER_ID), None)
    )

    assert status == 200
    conn.commit.assert_called_once()


def test_si_falla_ec2_no_se_borra_el_registro(conectar, ec2):
    conn, cursor = conectar
    cursor.fetchone.return_value = (1,)
    ec2.terminate_instances.side_effect = error_ec2("UnauthorizedOperation")

    status, body = leer_respuesta(
        borrar.lambda_handler(evento_delete("1", SERVER_ID), None)
    )

    assert status == 500
    assert body["error"] == "Internal Server Error"
    conn.commit.assert_not_called()
    conn.close.assert_called_once()
