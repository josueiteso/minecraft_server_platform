from unittest.mock import MagicMock

import pytest
from conftest import (
    SECRETO_PRUEBA,
    cargar_lambda,
    evento_api,
    firmar_token,
    leer_respuesta,
)

crear = cargar_lambda("create_server")

TOKEN = firmar_token("1")
DATOS = {
    "serverName": "Prueba",
    "minecraftVersion": "1.21.11",
    "gameMode": "survival",
    "maxPlayers": 5,
}


@pytest.fixture
def ec2(monkeypatch):
    cliente = MagicMock()
    cliente.run_instances.return_value = {"Instances": [{"InstanceId": "i-0abc12345"}]}
    monkeypatch.setattr(crear, "ec2_client", cliente)
    monkeypatch.setattr(crear, "get_security_group_id", lambda: "sg-123")
    monkeypatch.setattr(crear, "get_jwt_secret", lambda: SECRETO_PRUEBA)
    monkeypatch.setattr(crear, "register_server", lambda *args: None)
    return cliente


@pytest.mark.parametrize(
    "plan, instancia, memoria",
    [
        ("piedra", "t3.small", "-Xmx1G"),
        ("hierro", "t3.medium", "-Xmx2560M"),
        ("diamante", "t3.large", "-Xmx6G"),
    ],
)
def test_cada_plan_usa_su_instancia_y_memoria(ec2, plan, instancia, memoria):
    status, body = leer_respuesta(
        crear.lambda_handler(evento_api({**DATOS, "plan": plan}, TOKEN), None)
    )

    argumentos = ec2.run_instances.call_args.kwargs
    assert status == 202
    assert body["plan"] == plan
    assert body["instanceType"] == instancia
    assert argumentos["InstanceType"] == instancia
    assert memoria in argumentos["UserData"]


def test_sin_plan_usa_hierro(ec2):
    status, _ = leer_respuesta(crear.lambda_handler(evento_api(DATOS, TOKEN), None))

    assert status == 202
    assert ec2.run_instances.call_args.kwargs["InstanceType"] == "t3.medium"


def test_plan_que_no_existe_regresa_400(ec2):
    status, body = leer_respuesta(
        crear.lambda_handler(evento_api({**DATOS, "plan": "oro"}, TOKEN), None)
    )

    assert status == 400
    assert "plan" in body["error"]
    ec2.run_instances.assert_not_called()


@pytest.mark.parametrize(
    "plan, maximo",
    [("piedra", 5), ("hierro", 15), ("diamante", 30)],
)
def test_limite_de_jugadores_por_plan(ec2, plan, maximo):
    al_limite = {**DATOS, "plan": plan, "maxPlayers": maximo}
    de_mas = {**DATOS, "plan": plan, "maxPlayers": maximo + 1}

    status_ok, _ = leer_respuesta(
        crear.lambda_handler(evento_api(al_limite, TOKEN), None)
    )
    status_error, body = leer_respuesta(
        crear.lambda_handler(evento_api(de_mas, TOKEN), None)
    )

    assert status_ok == 202
    assert status_error == 400
    assert str(maximo) in body["error"]
