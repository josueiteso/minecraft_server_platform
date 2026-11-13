// Todas las llamadas al API Gateway pasan por aqui.
// Cada funcion regresa { status, datos } con el codigo HTTP y el JSON de la respuesta.
import { API_URL } from "./config.js";
import { leerSesion, sesionExpirada } from "./comun.js";

const MODO_EJEMPLO = API_URL === "";

async function pedir(metodo, ruta, cuerpo) {
  // si hay sesion, el token va en cada peticion
  const token = leerSesion()?.token;
  const headers = {};
  if (cuerpo) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  const respuesta = await fetch(`${API_URL}${ruta}`, {
    method: metodo,
    headers: Object.keys(headers).length ? headers : undefined,
    body: cuerpo ? JSON.stringify(cuerpo) : undefined,
  });
  // 401 con sesion = el token vencio o es invalido. hay que volver a iniciar sesion
  if (respuesta.status === 401 && token) sesionExpirada();
  let datos = {};
  try {
    datos = await respuesta.json();
  } catch {
    // respuesta sin JSON
  }
  return { status: respuesta.status, datos };
}

// POST /register -> register_user
export function registrar(username, email, password) {
  if (MODO_EJEMPLO) return ejemploRegistro(username);
  return pedir("POST", "/register", { username, email, password });
}

// POST /login -> login_user
export function iniciarSesion(email, password) {
  if (MODO_EJEMPLO) return ejemploLogin(email, password);
  return pedir("POST", "/login", { email, password });
}

// GET /servers?userId= -> list_servers
export function listarServidores(userId) {
  if (MODO_EJEMPLO) return ejemploServidores();
  return pedir("GET", `/servers?userId=${encodeURIComponent(userId)}`);
}

// POST /servers -> create_server
// plan: piedra, hierro o diamante. opUsername es opcional: si viene, el servidor se crea
// con comandos para esa cuenta de Minecraft
export function crearServidor(serverName, minecraftVersion, gameMode, maxPlayers, opUsername, plan) {
  const datos = { serverName, minecraftVersion, gameMode, maxPlayers, plan };
  if (opUsername) datos.opUsername = opUsername;
  if (MODO_EJEMPLO) return ejemploCrear(datos);
  return pedir("POST", "/servers", datos);
}

// POST /servers/control -> server_control (action: start, stop, backup o status)
export function controlarServidor(userId, serverId, action) {
  if (MODO_EJEMPLO) return ejemploControl(serverId, action);
  return pedir("POST", "/servers/control", { userId, serverId, action });
}

// DELETE /servers?userId=&serverId= -> delete_server (termina la instancia y la quita de la lista)
export function borrarServidor(userId, serverId) {
  if (MODO_EJEMPLO) return ejemploBorrar(serverId);
  const query = `userId=${encodeURIComponent(userId)}&serverId=${encodeURIComponent(serverId)}`;
  return pedir("DELETE", `/servers?${query}`);
}

// ---------- Datos de ejemplo (cuando config.js no tiene URL) ----------

const esperar = (ms) => new Promise((resolver) => setTimeout(resolver, ms));

async function ejemploRegistro(username) {
  await esperar(700);
  if (username.toLowerCase() === "usuario_existente") {
    return { status: 409, datos: { error: "El username o el email ya existen" } };
  }
  return { status: 201, datos: { message: "Usuario registrado", userId: "1", username } };
}

async function ejemploLogin(email, password) {
  await esperar(700);
  if (password === "incorrecta") {
    return { status: 401, datos: { error: "Credenciales invalidas" } };
  }
  return { status: 200, datos: { userId: "1", username: email.split("@")[0], token: "token-ejemplo" } };
}

// mismo formato que regresa list_servers
async function ejemploServidores() {
  await esperar(700);
  const servidor = (serverId, serverName, status, publicIp, createdAt, dbStatus = status) => ({
    serverId,
    serverName,
    status,
    dbStatus,
    publicIp,
    address: publicIp ? `${publicIp}:25565` : null,
    instanceType: "t3.medium",
    createdAt,
  });
  const base = [
    ...leer(CLAVE_CREADOS, []).map((s) =>
      ({
        ...servidor(s.id, s.nombre, "pending", null, new Date(s.creado).toISOString(), "creating"),
        instanceType: s.instancia || "t3.medium",
      }),
    ),
    servidor("i-0a1b2c3d4e5f60001", "Survival con amigos", "running", "54.210.33.12", "2026-03-12T18:20:00"),
    servidor("i-0a1b2c3d4e5f60002", "Mundo creativo", "stopped", null, "2026-03-04T21:05:00"),
    servidor("i-0a1b2c3d4e5f60003", "Aventura nocturna", "pending", null, "2026-02-28T17:40:00"),
    servidor("i-0a1b2c3d4e5f60004", "Isla del norte", "stopping", "18.201.65.84", "2026-02-16T10:11:00"),
  ];
  const borrados = leer(CLAVE_BORRADOS, []);
  const servers = base.filter((s) => !borrados.includes(s.serverId)).map(aplicarCambios);
  return { status: 200, datos: { servers } };
}

// los cambios del modo ejemplo se guardan mientras la pestana este abierta
const CLAVE_CREADOS = "servidores-ejemplo";
const CLAVE_CAMBIOS = "servidores-ejemplo-cambios";
const CLAVE_BORRADOS = "servidores-ejemplo-borrados";
const SEGUNDOS_TRANSICION = 6;

function leer(clave, porDefecto) {
  try {
    return JSON.parse(sessionStorage.getItem(clave)) ?? porDefecto;
  } catch {
    return porDefecto;
  }
}

function guardarCambio(serverId, cambio) {
  const cambios = leer(CLAVE_CAMBIOS, {});
  cambios[serverId] = { ...cambio, desde: Date.now() };
  sessionStorage.setItem(CLAVE_CAMBIOS, JSON.stringify(cambios));
}

// simula lo que haria EC2: pending -> running con IP nueva, stopping -> stopped sin IP
function aplicarCambios(servidor) {
  const creado = leer(CLAVE_CREADOS, []).find((s) => s.id === servidor.serverId);
  const cambio =
    leer(CLAVE_CAMBIOS, {})[servidor.serverId] ||
    (creado ? { status: "pending", desde: creado.creado, creando: true } : null);
  if (!cambio) return servidor;

  const terminado = Date.now() - cambio.desde > SEGUNDOS_TRANSICION * 1000;
  let { status } = cambio;
  if (terminado && status === "pending") status = "running";
  if (terminado && status === "stopping") status = "stopped";

  let publicIp = null;
  if (status === "running") publicIp = ipDeEjemplo(servidor.serverId, cambio.desde);
  if (status === "stopping") publicIp = servidor.publicIp;
  return {
    ...servidor,
    status,
    dbStatus: cambio.creando && status === "pending" ? "creating" : status,
    publicIp,
    address: publicIp ? `${publicIp}:25565` : null,
  };
}

// una IP distinta cada vez que se enciende
function ipDeEjemplo(serverId, desde) {
  const n = (desde + serverId.length) % 200;
  return `3.92.${20 + (n % 180)}.${30 + ((n * 7) % 200)}`;
}

const INSTANCIA_DE_EJEMPLO = { piedra: "t3.small", hierro: "t3.medium", diamante: "t3.large" };

async function ejemploCrear({ serverName, plan }) {
  await esperar(1500);
  if (serverName.toLowerCase() === "error") {
    return { status: 500, datos: { error: "Internal Server Error" } };
  }
  const id = `i-0ejemplo${Date.now().toString(16)}`;
  const instancia = INSTANCIA_DE_EJEMPLO[plan] || "t3.medium";
  const creados = [{ id, nombre: serverName, creado: Date.now(), instancia }, ...leer(CLAVE_CREADOS, [])];
  sessionStorage.setItem(CLAVE_CREADOS, JSON.stringify(creados));
  return {
    status: 202,
    datos: { message: "Servidor en proceso de creacion.", instanceId: id, status: "creating" },
  };
}

// mismas respuestas que server_control
async function ejemploControl(serverId, action) {
  const { datos } = await ejemploServidores();
  const servidor = datos.servers.find((s) => s.serverId === serverId);
  if (!servidor) return { status: 404, datos: { error: "Servidor no encontrado" } };

  if (action === "start") {
    guardarCambio(serverId, { status: "pending" });
    return { status: 200, datos: { message: "Servidor encendiendose", status: "starting" } };
  }
  if (action === "stop") {
    if (servidor.status !== "running") {
      return { status: 409, datos: { error: `El servidor esta en estado '${servidor.status}'` } };
    }
    await esperar(2500); // respaldo antes de apagar
    guardarCambio(serverId, { status: "stopping" });
    return { status: 200, datos: { message: "Respaldo guardado y servidor apagandose", status: "stopping" } };
  }
  if (action === "backup") {
    if (servidor.status !== "running") {
      return { status: 409, datos: { error: "Solo se puede respaldar un servidor encendido" } };
    }
    await esperar(2500);
    return { status: 200, datos: { message: "Respaldo guardado", backupKey: `backups/1/${serverId}/ejemplo.tar.gz` } };
  }
  return { status: 200, datos: { instanceId: serverId, status: servidor.status } };
}

// mismas respuestas que delete_server
async function ejemploBorrar(serverId) {
  const { datos } = await ejemploServidores();
  if (!datos.servers.some((s) => s.serverId === serverId)) {
    return { status: 404, datos: { error: "Servidor no encontrado" } };
  }
  await esperar(1200);
  sessionStorage.setItem(CLAVE_BORRADOS, JSON.stringify([...leer(CLAVE_BORRADOS, []), serverId]));
  return { status: 200, datos: { message: "Servidor eliminado", serverId } };
}
