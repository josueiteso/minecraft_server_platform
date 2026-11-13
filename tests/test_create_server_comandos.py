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
    "maxPlayers": 10,
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


def user_data_enviado(ec2):
    return ec2.run_instances.call_args.kwargs["UserData"]


def test_sin_comandos_no_hay_operador(ec2):
    status, _ = leer_respuesta(crear.lambda_handler(evento_api(DATOS, TOKEN), None))

    assert status == 202
    assert "ops.json" not in user_data_enviado(ec2)
    assert "enable-command-block" not in user_data_enviado(ec2)


def test_con_comandos_la_cuenta_queda_como_operador(ec2):
    datos = {**DATOS, "opUsername": "Steve_22"}

    status, _ = leer_respuesta(crear.lambda_handler(evento_api(datos, TOKEN), None))

    script = user_data_enviado(ec2)
    assert status == 202
    assert "api.mojang.com/users/profiles/minecraft/Steve_22" in script
    assert "ops.json" in script
    assert "enable-command-block=true" in script
    # el operador se escribe antes de arrancar el servidor
    assert script.index("ops.json") < script.index("systemctl enable --now minecraft")


@pytest.mark.parametrize(
    "nombre", ["ab", "nombre_de_mas_de_16", "steve; rm -rf /", "steve builder"]
)
def test_nombre_de_minecraft_invalido_regresa_400(ec2, nombre):
    datos = {**DATOS, "opUsername": nombre}

    status, body = leer_respuesta(crear.lambda_handler(evento_api(datos, TOKEN), None))

    assert status == 400
    assert body["error"] == "opUsername invalido"
    ec2.run_instances.assert_not_called()
