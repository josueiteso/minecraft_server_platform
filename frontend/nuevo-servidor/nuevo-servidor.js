import { crearServidor } from "../recursos-compartidos/api.js";
import { pintarUsuario, requerirSesion } from "../recursos-compartidos/comun.js";

// mismas reglas que valida create_server
const NOMBRE_VALIDO = /^[A-Za-z0-9 _-]+$/;
const OPERADOR_VALIDO = /^[A-Za-z0-9_]{3,16}$/;

function iniciarNuevoServidor() {
  const sesion = requerirSesion();
  if (!sesion) return;
  pintarUsuario(sesion);

  const formulario = document.getElementById("form-nuevo");
  const { nombre, version, jugadores, comandos, operador } = formulario.elements;
  const errorNombre = formulario.querySelector('[data-error="nombre"]');
  const errorOperador = formulario.querySelector('[data-error="operador"]');
  const campoOperador = document.getElementById("campo-operador");
  const botonesModo = formulario.querySelectorAll("[data-modo]");
  const botonesPlan = formulario.querySelectorAll("[data-plan]");
  const menos = document.getElementById("menos-jugadores");
  const mas = document.getElementById("mas-jugadores");
  const aviso = document.getElementById("creando");
  const toast = document.getElementById("error-crear");
  const boton = formulario.querySelector("button[type=submit]");
  const textoBoton = boton.innerHTML;
  let modo = "survival";
  // el plan define la instancia en create_server y el maximo de jugadores que acepta
  let plan = formulario.querySelector("[data-plan].selected");
  const maximoJugadores = () => Number(plan.dataset.max);

  const actualizarResumen = () => {
    document.getElementById("resumen-nombre").textContent = nombre.value.trim() || "Tu nuevo servidor";
    document.getElementById("resumen-version").textContent = version.value;
    document.getElementById("resumen-modo").textContent = modo[0].toUpperCase() + modo.slice(1);
    document.getElementById("resumen-jugadores").textContent = `${jugadores.value} máximo`;
    // la seccion de comandos esta comentada en el HTML; si no existe, se crea sin comandos
    if (comandos) document.getElementById("resumen-comandos").textContent = comandos.checked ? "Sí" : "No";
    const nombrePlan = plan.dataset.plan[0].toUpperCase() + plan.dataset.plan.slice(1);
    document.getElementById("resumen-plan").textContent = `${nombrePlan} · ${plan.dataset.ram}`;
    document.getElementById("resumen-precio").textContent = plan.dataset.precio;
    document.getElementById("limite-jugadores").textContent =
      `Entre 1 y ${maximoJugadores()} con el plan ${nombrePlan}`;
    jugadores.max = maximoJugadores();
    menos.disabled = Number(jugadores.value) <= 1;
    mas.disabled = Number(jugadores.value) >= maximoJugadores();
  };

  const ponerJugadores = (n) => {
    jugadores.value = Math.min(maximoJugadores(), Math.max(1, Number(n) || 1));
    actualizarResumen();
  };

  const mostrarErrorNombre = (mensaje) => {
    errorNombre.textContent = mensaje;
    errorNombre.hidden = !mensaje;
    nombre.setAttribute("aria-invalid", mensaje ? "true" : "false");
  };

  const mostrarErrorOperador = (mensaje) => {
    errorOperador.textContent = mensaje;
    errorOperador.hidden = !mensaje;
    operador.setAttribute("aria-invalid", mensaje ? "true" : "false");
  };

  if (comandos) {
    comandos.addEventListener("change", () => {
      campoOperador.hidden = !comandos.checked;
      mostrarErrorOperador("");
      if (comandos.checked) operador.focus();
      actualizarResumen();
    });
    operador.addEventListener("input", () => mostrarErrorOperador(""));
  }

  nombre.addEventListener("input", () => {
    mostrarErrorNombre("");
    actualizarResumen();
  });
  version.addEventListener("change", actualizarResumen);
  jugadores.addEventListener("change", () => ponerJugadores(jugadores.value));
  menos.addEventListener("click", () => ponerJugadores(Number(jugadores.value) - 1));
  mas.addEventListener("click", () => ponerJugadores(Number(jugadores.value) + 1));

  botonesModo.forEach((b) => {
    b.addEventListener("click", () => {
      modo = b.dataset.modo;
      botonesModo.forEach((otro) => {
        otro.classList.toggle("selected", otro === b);
        otro.setAttribute("aria-checked", otro === b ? "true" : "false");
      });
      actualizarResumen();
    });
  });

  botonesPlan.forEach((b) => {
    b.addEventListener("click", () => {
      plan = b;
      botonesPlan.forEach((otro) => {
        otro.classList.toggle("selected", otro === b);
        otro.setAttribute("aria-checked", otro === b ? "true" : "false");
      });
      // si habia mas jugadores de los que aguanta el plan, se baja al maximo
      ponerJugadores(jugadores.value);
    });
  });

  toast.querySelector("button").addEventListener("click", () => (toast.hidden = true));

  formulario.addEventListener("submit", async (evento) => {
    evento.preventDefault();
    toast.hidden = true;

    const nombreServidor = nombre.value.trim();
    if (!nombreServidor) return mostrarErrorNombre("Escribe un nombre para tu servidor.");
    if (nombreServidor.length > 50 || !NOMBRE_VALIDO.test(nombreServidor)) {
      return mostrarErrorNombre("Usa hasta 50 caracteres: letras, números, espacios, - y _.");
    }
    const nombreOperador = comandos?.checked ? operador.value.trim() : "";
    if (comandos?.checked && !OPERADOR_VALIDO.test(nombreOperador)) {
      operador.focus();
      return mostrarErrorOperador("Escribe tu nombre de Minecraft: 3 a 16 letras, números o _.");
    }

    boton.disabled = true;
    boton.textContent = "Creando servidor…";
    aviso.hidden = false;
    try {
      const { status } = await crearServidor(
        sesion.userId,
        nombreServidor,
        version.value,
        modo,
        Number(jugadores.value),
        nombreOperador,
        plan.dataset.plan,
      );
      // create_server responde 202: la instancia se sigue creando en segundo plano
      if (status >= 200 && status < 300) {
        window.location.href = "../servidores/";
        return;
      }
    } catch {
      // se muestra el mismo mensaje de error
    }
    toast.hidden = false;
    aviso.hidden = true;
    boton.disabled = false;
    boton.innerHTML = textoBoton;
  });

  actualizarResumen();
}

iniciarNuevoServidor();
