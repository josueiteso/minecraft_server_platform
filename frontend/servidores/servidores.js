import { listarServidores } from "../recursos-compartidos/api.js";
import {
  copiarTexto,
  enTransicion,
  escapar,
  estadoDe,
  formatearFecha,
  pintarUsuario,
  requerirSesion,
} from "../recursos-compartidos/comun.js";

const SEGUNDOS_REFRESCO = 10;

function iniciarServidores() {
  const sesion = requerirSesion();
  if (!sesion) return;
  pintarUsuario(sesion);

  const cargando = document.getElementById("cargando");
  const vacio = document.getElementById("vacio");
  const lista = document.getElementById("lista");
  let temporizador = null;

  const mostrar = (seccion) => {
    cargando.hidden = seccion !== cargando;
    vacio.hidden = seccion !== vacio;
    lista.hidden = seccion !== lista;
  };

  const mostrarError = () => {
    vacio.querySelector("h2").textContent = "No se pudieron cargar tus servidores";
    vacio.querySelector("p").textContent = "Revisa tu conexión e intenta de nuevo.";
    const boton = vacio.querySelector("a");
    boton.textContent = "Reintentar";
    boton.href = "../servidores/";
    mostrar(vacio);
  };

  const cargar = async () => {
    try {
      const { status, datos } = await listarServidores(sesion.userId);
      if (status !== 200) return mostrarError();
      const servidores = datos.servers || [];
      if (servidores.length === 0) return mostrar(vacio);

      lista.innerHTML = servidores.map(tarjetaServidor).join("");
      mostrar(lista);

      // mientras alguno este encendiendo, apagando o creandose, se vuelve a consultar
      clearTimeout(temporizador);
      if (servidores.some(enTransicion)) {
        temporizador = setTimeout(cargar, SEGUNDOS_REFRESCO * 1000);
      }
    } catch {
      mostrarError();
    }
  };

  // copiar la IP de una tarjeta
  lista.addEventListener("click", async (evento) => {
    const boton = evento.target.closest("[data-copiar]");
    if (!boton) return;
    if (await copiarTexto(boton.dataset.copiar)) {
      const etiqueta = boton.querySelector("b");
      etiqueta.textContent = "Copiada";
      setTimeout(() => (etiqueta.textContent = "Copiar"), 1600);
    }
  });

  cargar();
}

function tarjetaServidor(servidor) {
  const estado = estadoDe(servidor);
  const icono = enTransicion(servidor)
    ? '<i class="status-spinner" aria-hidden="true"></i>'
    : '<i class="status-dot" aria-hidden="true"></i>';
  const ip = servidor.publicIp
    ? `<button data-copiar="${escapar(servidor.address)}" aria-label="Copiar IP de ${escapar(servidor.serverName)}">
         <code>${escapar(servidor.publicIp)}</code><b>Copiar</b>
       </button>`
    : "<span>Sin IP · se asigna al encender</span>";

  return `
    <article class="dashboard-server-card status-${estado.clase}">
      <span class="card-status-bar" aria-hidden="true"></span>
      <div class="dashboard-card-head">
        <span class="pixel-server-icon" aria-hidden="true"><i></i><i></i><i></i></span>
        <div class="dashboard-server-name">
          <p>SERVIDOR</p>
          <h2>${escapar(servidor.serverName)}</h2>
        </div>
        <span class="dashboard-status ${estado.clase}">${icono}${estado.texto}</span>
      </div>
      <div class="ip-copy-chip ${servidor.publicIp ? "" : "no-ip"}">${ip}</div>
      <p class="ip-change-note">La IP cambia cada vez que se enciende</p>
      <dl class="server-card-meta">
        <div><dt>Instancia</dt><dd>${escapar(servidor.instanceType || "—")}</dd></div>
        <div><dt>Creado</dt><dd>${formatearFecha(servidor.createdAt)}</dd></div>
      </dl>
      <a class="manage-server-button" href="../servidor/?id=${encodeURIComponent(servidor.serverId)}">
        Administrar <span aria-hidden="true">→</span>
      </a>
    </article>`;
}

iniciarServidores();
