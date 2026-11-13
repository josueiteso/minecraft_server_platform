import base64
import json

import pytest
from conftest import SECRETO_PRUEBA, cargar_lambda, evento_api, leer_respuesta
from psycopg2 import errors

login = cargar_lambda("login_user")
register = cargar_lambda("register_user")

PASSWORD = "minecraft123"


@pytest.fixture
def usuario_guardado(monkeypatch):
    # hash con el mismo formato que guarda register_user (con menos iteraciones para que sea rapido)
    monkeypatch.setattr(register, "HASH_ITERATIONS", 1000)
    return (7, "steve_builder", register.hash_password(PASSWORD))


@pytest.fixture
def conectar(monkeypatch, db_falsa):
    conn, _ = db_falsa
    monkeypatch.setattr(login, "connect_db", lambda: conn)
    return db_falsa


@pytest.mark.parametrize(
    "datos", [{}, {"email": "steve@correo.com"}, {"password": PASSWORD}]
)
def test_faltan_datos_regresa_400(datos):
    status, body = leer_respuesta(login.lambda_handler(evento_api(datos), None))
    assert status == 400
    assert body["error"] == "email y password son requeridos"


def test_login_correcto_regresa_200(conectar, usuario_guardado, monkeypatch):
    monkeypatch.setattr(login, "get_jwt_secret", lambda: SECRETO_PRUEBA)
    _, cursor = conectar
    cursor.fetchone.return_value = usuario_guardado

    status, body = leer_respuesta(
        login.lambda_handler(
            evento_api({"email": "Steve@Correo.com", "password": PASSWORD}), None
        )
    )

    assert status == 200
    assert body["userId"] == "7"
    assert body["username"] == "steve_builder"
    # el token es JWT (3 partes) y su "sub" es el id del usuario
    partes = body["token"].split(".")
    assert len(partes) == 3
    claims = json.loads(base64.urlsafe_b64decode(partes[1] + "=="))
    assert claims["sub"] == "7"
    assert claims["exp"] > claims["iat"]
    # busca el email en minusculas
    assert cursor.execute.call_args.args[1] == ("steve@correo.com",)


def test_password_incorrecta_regresa_401(conectar, usuario_guardado):
    _, cursor = conectar
    cursor.fetchone.return_value = usuario_guardado

    status, body = leer_respuesta(
        login.lambda_handler(
            evento_api({"email": "steve@correo.com", "password": "otra-password"}),
            None,
        )
    )
    assert status == 401
    assert body["error"] == login.INVALID_CREDENTIALS


def test_email_que_no_existe_regresa_401_con_el_mismo_mensaje(conectar):
    _, cursor = conectar
    cursor.fetchone.return_value = None

    status, body = leer_respuesta(
        login.lambda_handler(
            evento_api({"email": "nadie@correo.com", "password": PASSWORD}), None
        )
    )
    assert status == 401
    assert body["error"] == login.INVALID_CREDENTIALS


def test_sin_tabla_users_regresa_401(conectar):
    _, cursor = conectar
    cursor.execute.side_effect = errors.UndefinedTable()

    status, _ = leer_respuesta(
        login.lambda_handler(
            evento_api({"email": "steve@correo.com", "password": PASSWORD}), None
        )
    )
    assert status == 401


def test_verify_password_rechaza_formatos_desconocidos():
    assert not login.verify_password(PASSWORD, "texto-plano")
    assert not login.verify_password(PASSWORD, "md5$1$aa$bb")
