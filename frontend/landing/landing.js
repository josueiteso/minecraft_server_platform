import { activarEnlaces, animarAlAparecer, copiarTexto } from "../recursos-compartidos/comun.js";

const ETIQUETAS_PROFUNDIDAD = ["Superficie", "-10 m", "-30 m", "-60 m"];

function iniciarLanding() {
  activarEnlaces();
  animarAlAparecer();

  // indicador de profundidad: se llena con el scroll y marca la capa visible
  const progreso = document.querySelector(".depth-progress");
  const etiqueta = document.querySelector(".depth-label");
  const puntos = document.querySelectorAll(".depth-track a");
  const capas = [...document.querySelectorAll("[data-depth]")];
  const enlacesMenu = document.querySelectorAll(".nav-links a");

  const alHacerScroll = () => {
    const maximo = document.documentElement.scrollHeight - window.innerHeight;
    progreso.style.height = `${maximo > 0 ? (window.scrollY / maximo) * 100 : 0}%`;

    const linea = window.scrollY + window.innerHeight * 0.44;
    const indice = capas.findIndex(
      (capa) => linea >= capa.offsetTop && linea < capa.offsetTop + capa.offsetHeight,
    );
    if (indice >= 0 && indice < ETIQUETAS_PROFUNDIDAD.length) {
      etiqueta.textContent = ETIQUETAS_PROFUNDIDAD[indice];
      puntos.forEach((punto, i) => punto.classList.toggle("active", i === indice));
    }
    // el boton del menu de la seccion visible queda marcado
    const seccion = indice >= 0 ? `#${capas[indice].id}` : "";
    enlacesMenu.forEach((enlace) => enlace.classList.toggle("active", enlace.getAttribute("href") === seccion));
  };
  alHacerScroll();
  window.addEventListener("scroll", alHacerScroll, { passive: true });

  // boton de copiar la IP de la tarjeta de ejemplo
  const botonCopiar = document.getElementById("copiar-ip");
  const icono = botonCopiar.querySelector("span");
  const iconoOriginal = icono.innerHTML;
  botonCopiar.addEventListener("click", async () => {
    if (await copiarTexto("54.210.33.12:25565")) {
      icono.textContent = "¡Copiada!";
      setTimeout(() => (icono.innerHTML = iconoOriginal), 1800);
    }
  });
}

iniciarLanding();
