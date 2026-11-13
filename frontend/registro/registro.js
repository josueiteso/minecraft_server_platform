import { iniciarSesion, registrar } from "../recursos-compartidos/api.js";
import { REGLAS, iniciarFormulario } from "../recursos-compartidos/formulario.js";

// register_user no da token, asi que al crear la cuenta se inicia sesion con los mismos datos.
async function registrarEIniciarSesion(v) {
  const registro = await registrar(v.username, v.email, v.password);
  if (registro.status !== 201) return registro;
  return iniciarSesion(v.email, v.password);
}

function iniciarRegistro() {
  iniciarFormulario({
    formulario: document.getElementById("form-registro"),
    reglas: { username: REGLAS.username, email: REGLAS.email, password: REGLAS.passwordNueva },
    textoCargando: "Creando cuenta…",
    enviar: registrarEIniciarSesion,
    statusOk: 200,
    mensajes: { 409: "El usuario o el correo ya existen" },
  });
}

iniciarRegistro();
