import pytest
from conftest import cargar_lambda, evento_api, leer_respuesta
from psycopg2 import errors

register = cargar_lambda("register_user")

DATOS_VALIDOS = {
    "username": "steve_builder",
    "email": "Steve@Correo.com",
    "password": "minecraft123",
}


@pytest.fixture(autouse=True)
def hash_rapido(monkeypatch):
    # 600 mil iteraciones hacen lentas las pruebas; el formato del hash no cambia
    monkeypatch.setattr(register, "HASH_ITERATIONS", 1000)


@pytest.fixture
def conectar(monkeypatch, db_falsa):
    conn, _ = db_falsa
    monkeypatch.setattr(register, "connect_db", lambda: conn)
    return db_falsa


@pytest.mark.parametrize(
    "cambio, mensaje",
    [
        ({"username": "ab"}, "username"),
        ({"username": "steve builder"}, "username"),
        ({"email": "correo-sin-arroba"}, "email"),
        ({"password": "corta"}, "password"),
    ],
)
def test_datos_invalidos_regresa_400(cambio, mensaje):
    status, body = leer_respuesta(
        register.lambda_handler(evento_api({**DATOS_VALIDOS, **cambio}), None)
    )
    assert status == 400
    assert mensaje in body["error"]


def test_json_invalido_regresa_400():
    status, body = leer_respuesta(
        register.lambda_handler({"body": "{no es json"}, None)
    )
    assert status == 400
    assert body["error"] == "JSON invalido"


def test_registro_correcto_regresa_201(conectar):
    conn, cursor = conectar
    cursor.fetchone.return_value = (7,)

    status, body = leer_respuesta(
        register.lambda_handler(evento_api(DATOS_VALIDOS), None)
    )

    assert status == 201
    assert body["userId"] == "7"
    assert body["username"] == "steve_builder"
    conn.commit.assert_called_once()
    conn.close.assert_called_once()


def test_la_password_se_guarda_con_hash(conectar):
    _, cursor = conectar
    cursor.fetchone.return_value = (7,)

    register.lambda_handler(evento_api(DATOS_VALIDOS), None)

    # el ultimo execute es el INSERT; el email se guarda en minusculas
    _, valores = cursor.execute.call_args.args
    _, email, password_hash = valores
    assert email == "steve@correo.com"
    assert password_hash != DATOS_VALIDOS["password"]
    assert password_hash.startswith("pbkdf2_sha256$")


def test_usuario_repetido_regresa_409(conectar):
    conn, cursor = conectar
    cursor.execute.side_effect = [None, errors.UniqueViolation()]

    status, body = leer_respuesta(
        register.lambda_handler(evento_api(DATOS_VALIDOS), None)
    )

    assert status == 409
    assert body["error"] == "El username o el email ya existen"
    conn.rollback.assert_called_once()


def test_error_de_base_de_datos_regresa_500(monkeypatch):
    def falla():
        raise RuntimeError("RDS no disponible")

    monkeypatch.setattr(register, "connect_db", falla)

    status, body = leer_respuesta(
        register.lambda_handler(evento_api(DATOS_VALIDOS), None)
    )
    assert status == 500
    assert body["error"] == "Internal Server Error"
