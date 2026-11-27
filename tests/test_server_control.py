from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from conftest import (
    SECRETO_PRUEBA,
    cargar_lambda,
    evento_api,
    firmar_token,
    leer_respuesta,
)

control = cargar_lambda("server_control")

SERVER_ID = "i-0309ed93dd6aec9af"


def evento_control(action, server_id=SERVER_ID, user_id="1"):
    # POST /servers/control como lo manda API Gateway; el usuario va en el token
    return evento_api(
        {"serverId": server_id, "action": action}, token=firmar_token(user_id)
    )


def instancias(estado):
    return {"Reservations": [{"Instances": [{"State": {"Name": estado}}]}]}


def error_ec2(codigo):
    return ClientError({"Error": {"Code": codigo, "Message": codigo}}, "Operacion")


@pytest.fixture(autouse=True)
def secreto(monkeypatch):
    monkeypatch.setattr(control, "get_jwt_secret", lambda: SECRETO_PRUEBA)


# las metricas no salen a CloudWatch; se guardan en una lista para revisarlas
@pytest.fixture(autouse=True)
def cloudwatch(monkeypatch):
    cliente = MagicMock()
    monkeypatch.setattr(control, "cloudwatch_client", cliente)
    return cliente


def metricas(cloudwatch):
    datos = []
    for llamada in cloudwatch.put_metric_data.call_args_list:
        datos += llamada.kwargs["MetricData"]
    return [d["MetricName"] for d in datos if "Dimensions" not in d]


@pytest.fixture
def conectar(monkeypatch, db_falsa):
    conn, cursor = db_falsa
    cursor.fetchone.return_value = (1,)  # el servidor es del usuario
    monkeypatch.setattr(control, "connect_db", lambda: conn)
    return db_falsa


@pytest.fixture
def ec2(monkeypatch):
    cliente = MagicMock()
    monkeypatch.setattr(control, "ec2_client", cliente)
    return cliente


def test_sin_token_regresa_401(cloudwatch):
    evento = evento_api({"serverId": SERVER_ID, "action": "start"})

    status, body = leer_respuesta(control.lambda_handler(evento, None))

    assert status == 401
    assert body["error"] == "Sesion invalida o expirada"
    assert "ControlAuthFailures" in metricas(cloudwatch)


def test_token_vencido_regresa_401():
    evento = evento_api(
        {"serverId": SERVER_ID, "action": "start"},
        token=firmar_token("1", vence_en=-10),
    )

    status, _ = leer_respuesta(control.lambda_handler(evento, None))

    assert status == 401


@pytest.mark.parametrize(
    "action, server_id, mensaje",
    [
        ("backup", SERVER_ID, "action debe ser"),
        ("borrar", SERVER_ID, "action debe ser"),
        ("start", "i-123; DROP TABLE servers", "serverId invalido"),
    ],
)
def test_datos_invalidos_regresa_400(action, server_id, mensaje):
    status, body = leer_respuesta(
        control.lambda_handler(evento_control(action, server_id), None)
    )

    assert status == 400
    assert mensaje in body["error"]


def test_servidor_de_otro_usuario_regresa_404(conectar, ec2):
    _, cursor = conectar
    cursor.fetchone.return_value = None

    status, _ = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 404
    ec2.start_instances.assert_not_called()


def test_el_dueno_sale_del_token(conectar, ec2):
    _, cursor = conectar
    ec2.describe_instances.return_value = instancias("stopped")

    control.lambda_handler(evento_control("start", user_id="42"), None)

    consulta, valores = cursor.execute.call_args_list[0].args
    assert consulta.startswith("SELECT 1 FROM servers")
    assert valores == (SERVER_ID, "42")


def test_encender_servidor_apagado(conectar, ec2, cloudwatch):
    conn, cursor = conectar
    ec2.describe_instances.return_value = instancias("stopped")

    status, body = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 200
    assert body["status"] == "starting"
    ec2.start_instances.assert_called_once_with(InstanceIds=[SERVER_ID])
    assert cursor.execute.call_args.args[1] == ("starting", SERVER_ID)
    conn.commit.assert_called_once()
    assert "ServersStarted" in metricas(cloudwatch)


def test_encender_servidor_ya_encendido_no_llama_a_ec2(conectar, ec2):
    ec2.describe_instances.return_value = instancias("running")

    status, body = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 200
    assert body["status"] == "running"
    ec2.start_instances.assert_not_called()


def test_apagar_servidor_encendido_sin_respaldo(conectar, ec2, cloudwatch):
    _, cursor = conectar
    ec2.describe_instances.return_value = instancias("running")

    status, body = leer_respuesta(control.lambda_handler(evento_control("stop"), None))

    assert status == 200
    assert body["status"] == "stopping"
    ec2.stop_instances.assert_called_once_with(InstanceIds=[SERVER_ID])
    assert cursor.execute.call_args.args[1] == ("stopping", SERVER_ID)
    assert "ServersStopped" in metricas(cloudwatch)


def test_apagar_servidor_ya_apagado(conectar, ec2):
    ec2.describe_instances.return_value = instancias("stopped")

    status, _ = leer_respuesta(control.lambda_handler(evento_control("stop"), None))

    assert status == 200
    ec2.stop_instances.assert_not_called()


@pytest.mark.parametrize("action, estado", [("start", "stopping"), ("stop", "pending")])
def test_servidor_cambiando_de_estado_regresa_409(
    conectar, ec2, cloudwatch, action, estado
):
    ec2.describe_instances.return_value = instancias(estado)

    status, body = leer_respuesta(control.lambda_handler(evento_control(action), None))

    assert status == 409
    assert estado in body["error"]
    assert "ControlConflicts" in metricas(cloudwatch)


def test_instancia_que_ya_no_existe_regresa_409(conectar, ec2):
    ec2.describe_instances.side_effect = error_ec2("InvalidInstanceID.NotFound")

    status, body = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 409
    assert "terminated" in body["error"]


def test_estado_actualiza_la_tabla(conectar, ec2):
    _, cursor = conectar
    ec2.describe_instances.return_value = instancias("running")

    status, body = leer_respuesta(
        control.lambda_handler(evento_control("status"), None)
    )

    assert status == 200
    assert body == {"instanceId": SERVER_ID, "status": "running"}
    assert cursor.execute.call_args.args[1] == ("running", SERVER_ID)


def test_si_falla_ec2_regresa_500_y_cuenta_la_falla(conectar, ec2, cloudwatch):
    conn, _ = conectar
    ec2.describe_instances.return_value = instancias("stopped")
    ec2.start_instances.side_effect = error_ec2("InsufficientInstanceCapacity")

    status, body = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 500
    assert body["error"] == "Internal Server Error"
    conn.commit.assert_not_called()
    conn.close.assert_called_once()
    nombres = metricas(cloudwatch)
    assert "Ec2ControlFailures" in nombres
    assert "ControlServerErrors" in nombres


def test_si_falla_la_base_cuenta_la_etapa(monkeypatch, cloudwatch):
    def falla():
        raise RuntimeError("RDS no disponible")

    monkeypatch.setattr(control, "connect_db", falla)

    status, _ = leer_respuesta(control.lambda_handler(evento_control("start"), None))

    assert status == 500
    assert "ControlDbFailures" in metricas(cloudwatch)


def test_si_cloudwatch_falla_la_respuesta_no_cambia(conectar, ec2, cloudwatch):
    ec2.describe_instances.return_value = instancias("running")
    cloudwatch.put_metric_data.side_effect = RuntimeError("CloudWatch no disponible")

    status, _ = leer_respuesta(control.lambda_handler(evento_control("status"), None))

    assert status == 200
