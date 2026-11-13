// Funciones que usan todas las paginas.

// ---------- Sesion ----------
// Al iniciar sesion se guardan el userId y el username en el navegador.

const CLAVE_SESION = "sesion";

export function guardarSesion(userId, username, token) {
  localStorage.setItem(CLAVE_SESION, JSON.stringify({ userId, username, token }));
}

// una sesion guardada sin token (de antes de que existiera) no sirve. se pide iniciar sesion de nuevo
export function leerSesion() {
  try {
    const sesion = JSON.parse(localStorage.getItem(CLAVE_SESION));
    return sesion?.token ? sesion : null;
  } catch {
    return null;
  }
}

export function cerrarSesion() {
  localStorage.removeItem(CLAVE_SESION);
  window.location.href = "../";
}

// el token vencio o ya no es valido (401), se manda a login
export function sesionExpirada() {
  localStorage.removeItem(CLAVE_SESION);
  window.location.href = "../login/";
}

// ---------- Utilidades ----------

// Los botones con data-href="../pagina/" funcionan como enlaces.
export function activarEnlaces() {
  document.querySelectorAll("[data-href]").forEach((boton) => {
    boton.addEventListener("click", () => {
      window.location.href = boton.dataset.href;
    });
  });
}

// Copia un texto al portapapeles. Regresa true si se pudo.
export async function copiarTexto(texto) {
  try {
    await navigator.clipboard.writeText(texto);
    return true;
  } catch {
    // el sitio de S3 es http y ahi el navegador no deja usar navigator.clipboard;
    // se copia con un campo de texto oculto
    const campo = document.createElement("textarea");
    campo.value = texto;
    campo.setAttribute("readonly", "");
    campo.style.position = "fixed";
    campo.style.opacity = "0";
    document.body.append(campo);
    campo.select();
    try {
      return document.execCommand("copy");
    } catch {
      return false;
    } finally {
      campo.remove();
    }
  }
}

// Las tarjetas con la clase "reveal" aparecen cuando llegan a la pantalla.
export function animarAlAparecer() {
  const observador = new IntersectionObserver(
    (entradas) => {
      entradas.forEach((entrada) => {
        if (entrada.isIntersecting) entrada.target.classList.add("is-visible");
      });
    },
    { threshold: 0.14 },
  );
  document.querySelectorAll(".reveal").forEach((elemento) => observador.observe(elemento));
}

// ---------- Paginas del panel ----------

// Si no hay sesion manda al login. Regresa la sesion.
export function requerirSesion() {
  const sesion = leerSesion();
  if (!sesion) window.location.href = "../login/";
  return sesion;
}

// Llena el encabezado del panel: iniciales, saludo y boton de cerrar sesion.
export function pintarUsuario(sesion) {
  const partes = sesion.username.split(/[_\s.-]+/).filter(Boolean);
  const iniciales = partes.length > 1 ? partes[0][0] + partes[1][0] : sesion.username.slice(0, 2);
  document.getElementById("iniciales").textContent = iniciales.toUpperCase();
  document.getElementById("saludo").textContent = `Hola, ${sesion.username}`;
  document.getElementById("cerrar-sesion").addEventListener("click", cerrarSesion);
}

// Estado de EC2 (el que regresa la API) -> estado que se muestra.
const ESTADOS = {
  running: { clase: "on", texto: "Encendido" },
  stopped: { clase: "off", texto: "Apagado" },
  pending: { clase: "starting", texto: "Encendiendo" },
  stopping: { clase: "stopping", texto: "Apagando" },
  "shutting-down": { clase: "stopping", texto: "Eliminando" },
  terminated: { clase: "off", texto: "No disponible" },
};

export function estadoDe(servidor) {
  if (servidor.status === "pending" && servidor.dbStatus === "creating") {
    return { clase: "creating", texto: "Creando" };
  }
  return ESTADOS[servidor.status] || { clase: "off", texto: servidor.status };
}

// true mientras el servidor esta cambiando de estado
export function enTransicion(servidor) {
  return ["starting", "stopping", "creating", "backing-up"].includes(estadoDe(servidor).clase);
}

export function formatearFecha(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("es-MX", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

// Evita que un texto se interprete como HTML.
export function escapar(texto) {
  const div = document.createElement("div");
  div.textContent = texto ?? "";
  return div.innerHTML;
}
