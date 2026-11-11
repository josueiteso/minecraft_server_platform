import { registrar } from "../recursos-compartidos/api.js";
import { REGLAS, iniciarFormulario } from "../recursos-compartidos/formulario.js";

function iniciarRegistro() {
  iniciarFormulario({
    formulario: document.getElementById("form-registro"),
    reglas: { username: REGLAS.username, email: REGLAS.email, password: REGLAS.passwordNueva },
    textoCargando: "Creando cuenta…",
    enviar: (v) => registrar(v.username, v.email, v.password),
    statusOk: 201,
    mensajes: { 409: "El usuario o el correo ya existen" },
  });
}

iniciarRegistro();
