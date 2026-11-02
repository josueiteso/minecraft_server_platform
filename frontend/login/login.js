import { iniciarSesion } from "../recursos-compartidos/api.js";
import { REGLAS, iniciarFormulario } from "../recursos-compartidos/formulario.js";

function iniciarLogin() {
  iniciarFormulario({
    formulario: document.getElementById("form-login"),
    reglas: { email: REGLAS.email, password: REGLAS.password },
    textoCargando: "Entrando…",
    enviar: (v) => iniciarSesion(v.email, v.password),
    statusOk: 200,
    mensajes: { 401: "Correo o contraseña incorrectos" },
  });
}

iniciarLogin();
