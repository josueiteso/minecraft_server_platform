// Formulario de login y registro: validacion, ver contrasena, avisos y sesion.
import { guardarSesion, leerSesion } from "./comun.js";

const OJO_ABIERTO =
  "M12 4c7 0 10 8 10 8s-3 8-10 8S2 12 2 12s3-8 10-8Zm0 2c-5.2 0-7.8 6-7.8 6s2.6 6 7.8 6 7.8-6 7.8-6S17.2 6 12 6Zm0 2a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z";
const OJO_CERRADO =
  "M2 5.3 3.3 4l16.7 16.7-1.3 1.3-3-3A11.3 11.3 0 0 1 12 20C5 20 2 12 2 12a18 18 0 0 1 3.2-4.8L2 5.3Zm5.1 3.2A13.8 13.8 0 0 0 4.2 12s2.6 6 7.8 6c.8 0 1.5-.1 2.2-.4l-2-2a4 4 0 0 1-3.7-3.7L7.1 8.5ZM12 4c7 0 10 8 10 8a17.6 17.6 0 0 1-3.6 5.2L16.9 16a14.2 14.2 0 0 0 2.9-4S17.2 6 12 6c-.8 0-1.6.2-2.3.4L8.1 4.9A11.5 11.5 0 0 1 12 4Zm.2 4a4 4 0 0 1 3.8 3.8L12.2 8Z";

const icono = (ruta) =>
  `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="${ruta}" fill="currentColor" /></svg>`;

// mismas reglas que valida register_user en el backend
export const REGLAS = {
  username: (v) =>
    /^[A-Za-z0-9_]{3,30}$/.test(v) ? "" : "Usa entre 3 y 30 letras, números o _.",
  email: (v) =>
    /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v) ? "" : "Introduce un correo electrónico válido.",
  passwordNueva: (v) => (v.length >= 8 ? "" : "La contraseña debe tener al menos 8 caracteres."),
  password: (v) => (v ? "" : "Introduce tu contraseña."),
};

export function iniciarFormulario({ formulario, reglas, textoCargando, enviar, statusOk, mensajes }) {
  // si ya hay sesion, directo al panel
  if (leerSesion()) {
    window.location.href = "../servidores/";
    return;
  }

  const aviso = document.querySelector(".auth-error");
  const boton = formulario.querySelector("button[type=submit]");
  const textoBoton = boton.innerHTML;

  // mostrar u ocultar la contrasena
  const verPassword = formulario.querySelector(".ver-password");
  const campoPassword = formulario.elements.password;
  verPassword.innerHTML = icono(OJO_ABIERTO);
  verPassword.addEventListener("click", () => {
    const oculta = campoPassword.type === "password";
    campoPassword.type = oculta ? "text" : "password";
    verPassword.innerHTML = icono(oculta ? OJO_CERRADO : OJO_ABIERTO);
    verPassword.setAttribute("aria-label", oculta ? "Ocultar contraseña" : "Mostrar contraseña");
  });

  const mostrarError = (campo, mensaje) => {
    const em = formulario.querySelector(`[data-error="${campo}"]`);
    em.textContent = mensaje;
    em.hidden = !mensaje;
    formulario.elements[campo].setAttribute("aria-invalid", mensaje ? "true" : "false");
  };

  const mostrarAviso = (mensaje) => {
    aviso.querySelector("span").textContent = mensaje;
    aviso.hidden = !mensaje;
  };

  // al escribir se quita el error de ese campo
  Object.keys(reglas).forEach((campo) => {
    formulario.elements[campo].addEventListener("input", () => mostrarError(campo, ""));
  });

  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    mostrarAviso("");

    const valores = {};
    let valido = true;
    Object.entries(reglas).forEach(([campo, regla]) => {
      valores[campo] = formulario.elements[campo].value.trim();
      if (campo === "password") valores[campo] = formulario.elements[campo].value;
      const error = regla(valores[campo]);
      mostrarError(campo, error);
      if (error) valido = false;
    });
    if (!valido) return;

    boton.disabled = true;
    boton.textContent = textoCargando;
    try {
      const { status, datos } = await enviar(valores);
      if (status === statusOk) {
        guardarSesion(datos.userId, datos.username);
        window.location.href = "../servidores/";
        return;
      }
      mostrarAviso(mensajes[status] || datos.error || "Algo salió mal. Intenta de nuevo.");
    } catch {
      mostrarAviso("No se pudo conectar con el servidor. Intenta de nuevo.");
    }
    boton.disabled = false;
    boton.innerHTML = textoBoton;
  });
}
