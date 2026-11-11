import { borrarServidor, controlarServidor, listarServidores } from "../recursos-compartidos/api.js";
import {
  copiarTexto,
  enTransicion,
  escapar,
  estadoDe,
  formatearFecha,
  pintarUsuario,
  requerirSesion,
} from "../recursos-compartidos/comun.js";

const SEGUNDOS_REFRESCO_DETALLE = 5;
const CLAVE_RESPALDOS = "ultimos-respaldos";

function iniciarDetalle() {
  const sesion = requerirSesion();
  if (!sesion) return;
  pintarUsuario(sesion);

  const serverId = new URLSearchParams(window.location.search).get("id");
  if (!serverId) {
    window.location.href = "../servidores/";
    return;
  }

  const $ = (id) => document.getElementById(id);
  const botones = { encender: $("encender"), apagar: $("apagar"), respaldar: $("respaldar") };
  const modal = $("modal-apagar");
  const modalBorrar = $("modal-borrar");
  let borrando = false;
  let servidor = null;
  let accionLocal = null; // "backing-up" mientras se espera la respuesta del respaldo
  let temporizador = null;

  let temporizadorToast = null;
  const toastOk = (mensaje) => {
    const toast = $("toast-ok");
    toast.innerHTML = `<span aria-hidden="true">✓</span>${escapar(mensaje)}`;
    toast.hidden = false;
    clearTimeout(temporizadorToast);
    temporizadorToast = setTimeout(() => (toast.hidden = true), 2600);
  };
  const toastError = (mensaje) => {
    const toast = $("toast-error");
    toast.querySelector("p").textContent = mensaje;
    toast.hidden = false;
  };
  $("toast-error").querySelector("button").addEventListener("click", () => ($("toast-error").hidden = true));

  const ultimoRespaldo = () => {
    let respaldos = {};
    try {
      respaldos = JSON.parse(sessionStorage.getItem(CLAVE_RESPALDOS)) || {};
    } catch {
      // sin respaldos guardados
    }
    const fecha = respaldos[serverId];
    if (!fecha) return "Sin registro";
    const hora = new Date(fecha).toLocaleTimeString("es-MX", { hour: "2-digit", minute: "2-digit" });
    return `Hoy, ${hora}`;
  };
  const anotarRespaldo = () => {
    let respaldos = {};
    try {
      respaldos = JSON.parse(sessionStorage.getItem(CLAVE_RESPALDOS)) || {};
    } catch {
      // se empieza de cero
    }
    respaldos[serverId] = new Date().toISOString();
    sessionStorage.setItem(CLAVE_RESPALDOS, JSON.stringify(respaldos));
  };

  const pintar = () => {
    const estado = accionLocal === "backing-up" ? { clase: "backing-up", texto: "Respaldando" } : estadoDe(servidor);
    const ocupado = accionLocal !== null || enTransicion(servidor);
    const texto = `${estado.texto}${ocupado ? "…" : ""}`;

    document.title = `${servidor.serverName} · NODO`;
    $("nombre").textContent = servidor.serverName;
    const insignia = $("estado");
    insignia.className = `detail-status-badge dashboard-status ${estado.clase}`;
    insignia.innerHTML = `<i class="${ocupado ? "status-spinner" : "status-dot"}"></i>${escapar(texto)}`;

    // apagado: solo encender. encendido: apagar y respaldar. cambiando de estado: nada
    botones.encender.disabled = ocupado || estado.clase !== "off" || servidor.status === "terminated";
    botones.apagar.disabled = ocupado || estado.clase !== "on";
    botones.respaldar.disabled = ocupado || estado.clase !== "on";
    // se puede borrar en cualquier estado: delete_server termina la instancia como este
    $("borrar").disabled = borrando;

    const nota = $("nota-espera");
    nota.hidden = !ocupado;
    nota.querySelector("b").textContent = texto;
    $("nota-respaldo").hidden = estado.clase !== "backing-up";

    const ip = servidor.publicIp;
    $("panel-ip").classList.toggle("without-ip", !ip);
    $("ip").textContent = ip ? servidor.address || ip : "Sin IP · se asigna al encender";
    $("ip-ayuda").hidden = !ip;
    $("copiar-ip").hidden = !ip;

    $("info-instancia").textContent = servidor.instanceType || "—";
    $("info-fecha").textContent = formatearFecha(servidor.createdAt);
    $("info-respaldo").textContent = ultimoRespaldo();
  };

  const cargar = async () => {
    clearTimeout(temporizador);
    try {
      const { status, datos } = await listarServidores(sesion.userId);
      if (status !== 200) throw new Error();
      servidor = (datos.servers || []).find((s) => s.serverId === serverId);
      if (!servidor) {
        $("nombre").textContent = "Servidor no encontrado";
        return;
      }
      pintar();
      if (enTransicion(servidor)) {
        temporizador = setTimeout(cargar, SEGUNDOS_REFRESCO_DETALLE * 1000);
      }
    } catch {
      toastError("No se pudo cargar el servidor. Revisa tu conexión e intenta de nuevo.");
    }
  };

  // llama a server_control y muestra el error si algo falla
  const ejecutar = async (accion) => {
    try {
      const { status, datos } = await controlarServidor(sesion.userId, serverId, accion);
      if (status === 200) return datos;
      if (status === 409) toastError("El servidor está ocupado. Espera a que termine e intenta de nuevo.");
      else if (status === 404) toastError("No encontramos este servidor en tu cuenta.");
      else toastError("No se pudo completar la acción. Intenta de nuevo más tarde.");
    } catch {
      toastError("No se pudo conectar con el servidor. Intenta de nuevo.");
    }
    return null;
  };

  botones.encender.addEventListener("click", async () => {
    servidor = { ...servidor, status: "pending", dbStatus: "starting" };
    pintar();
    await ejecutar("start");
    cargar();
  });

  botones.apagar.addEventListener("click", () => (modal.hidden = false));
  $("cancelar-apagar").addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("mousedown", (evento) => {
    if (evento.target === modal) modal.hidden = true;
  });
  $("confirmar-apagar").addEventListener("click", async () => {
    modal.hidden = true;
    servidor = { ...servidor, status: "stopping" };
    pintar();
    // server_control respalda el mundo antes de apagar
    if (await ejecutar("stop")) anotarRespaldo();
    cargar();
  });

  botones.respaldar.addEventListener("click", async () => {
    accionLocal = "backing-up";
    pintar();
    const ok = await ejecutar("backup");
    accionLocal = null;
    if (ok) {
      anotarRespaldo();
      toastOk("Respaldo guardado");
    }
    pintar();
  });

  $("borrar").addEventListener("click", () => (modalBorrar.hidden = false));
  $("cancelar-borrar").addEventListener("click", () => (modalBorrar.hidden = true));
  modalBorrar.addEventListener("mousedown", (evento) => {
    if (evento.target === modalBorrar) modalBorrar.hidden = true;
  });
  $("confirmar-borrar").addEventListener("click", async () => {
    modalBorrar.hidden = true;
    borrando = true;
    clearTimeout(temporizador);
    $("borrar").innerHTML = '<span aria-hidden="true">✕</span> Borrando…';
    pintar();
    try {
      const { status } = await borrarServidor(sesion.userId, serverId);
      if (status === 200) {
        window.location.href = "../servidores/";
        return;
      }
      if (status === 404) toastError("No encontramos este servidor en tu cuenta.");
      else toastError("No se pudo borrar el servidor. Intenta de nuevo más tarde.");
    } catch {
      toastError("No se pudo conectar con el servidor. Intenta de nuevo.");
    }
    borrando = false;
    $("borrar").innerHTML = '<span aria-hidden="true">✕</span> Borrar';
    cargar();
  });

  $("copiar-ip").addEventListener("click", async () => {
    if (servidor?.address && (await copiarTexto(servidor.address))) {
      const boton = $("copiar-ip");
      const original = boton.innerHTML;
      boton.textContent = "Copiada";
      setTimeout(() => (boton.innerHTML = original), 1600);
    }
  });

  cargar();
}

iniciarDetalle();
