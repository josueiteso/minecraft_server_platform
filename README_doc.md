# Documentacion de la plataforma

Documentacion general de la plataforma: infraestructura, Lambdas, frontend, pruebas y monitoreo.

Aqui estan todos los comandos que se usaron para montar la plataforma. Todos los comandos de AWS CLI se corren en una terminal de Linux (WSL Ubuntu) con las credenciales del Learner Lab configuradas.

## 1. Lambda crear servidores (`create_server`)

Codigo: [`lambdas/create_server/lambda_function.py`](lambdas/create_server/lambda_function.py)

Ruta `POST /servers` con el token en `Authorization: Bearer <token>`. El token lo regresa `login_user` y dura 12 horas. El dueño del servidor sale del token, nunca del body, igual que en `delete_server` y `server_control`. La Lambda valida los datos, lanza una instancia EC2 con Ubuntu que instala y arranca el servidor de Minecraft, y lo registra en la tabla `servers`. Responde sin esperar a que el servidor termine de instalarse: la EC2 tarda unos minutos en descargar Java y el servidor, y mientras tanto se ve en `GET /servers`.

Todos los campos del body son opcionales:

| Campo              | Por defecto  | Validacion                                                                                                                                   |
| ------------------ | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `serverName`       | `mc-server`  | Se limpia a letras, numeros, espacios, `_` y `-`, con un maximo de 50 caracteres. Es el nombre de la instancia y el mensaje del dia (`motd`) |
| `minecraftVersion` | `1.21.10`    | Formato `1.21` o `1.21.10`. El script de arranque busca esa version en el manifiesto de Mojang y descarga su `server.jar`                    |
| `gameMode`         | `survival`   | `survival`, `creative`, `adventure` o `spectator`                                                                                            |
| `plan`             | `hierro`     | `piedra`, `hierro` o `diamante` (seccion 10)                                                                                                 |
| `maxPlayers`       | `10`         | Entre 1 y el limite del plan. Con `piedra` hay que mandarlo (5 o menos), porque el valor por defecto supera su limite                        |
| `opUsername`       | sin comandos | Nombre de Minecraft Java (3 a 16 letras, numeros o `_`). Ver "Comandos en el servidor" en la seccion 9                                       |

| Respuesta | Cuando                                                                                                     |
| --------- | ---------------------------------------------------------------------------------------------------------- |
| 202       | Instancia lanzada y servidor registrado, regresa `instanceId`, `plan`, `instanceType` y `status: creating` |
| 400       | El body no es JSON, o `minecraftVersion`, `gameMode`, `plan`, `maxPlayers` u `opUsername` son invalidos    |
| 401       | Sin token, token invalido o vencido                                                                        |
| 500       | Error interno (por ejemplo EC2 sin capacidad o sin cuota de vCPU, o RDS caida; detalle en CloudWatch Logs) |

Que hace, en orden:

1. Valida el token (HS256, firmado con el secreto `minecraft-jwt-secret` de Secrets Manager) y el body.
2. Arma el script de arranque (`UserData`) de la instancia: instala Java 21, descarga el servidor de Mojang, acepta el EULA, escribe `server.properties` (`gamemode`, `max-players` y `motd`), crea `ops.json` si hay `opUsername` y deja el servidor como servicio de systemd (`minecraft`) con la memoria de Java del plan.
3. Lanza la EC2 con `run_instances`, con el security group `minecraft-servers-sg` (puertos 25565 y 22) y las tags `Name`, `OwnerId` y `Plan`.
4. Registra el servidor en la tabla `servers` (la crea si no existe) con estado `creating`.
5. Si el registro falla, termina la instancia que acaba de lanzar para no dejar maquinas cobrando sin registro, y responde 500. Si esa terminacion tambien falla queda una instancia huerfana: salta la alarma `minecraft-create-rollback-failed` (seccion 13) y hay que terminarla a mano.

El Learner Lab permite como maximo 9 instancias EC2 encendidas a la vez, y cuentan todas las de la cuenta. Con 2 vCPU por servidor son 18 vCPU, que es el valor por defecto de la alarma de cuota (seccion 13). Al llegar al limite `run_instances` falla y la Lambda responde 500.

Se despliega con `./scripts/deploy.sh` (ruta `POST /servers`). Probar la creacion con la URL de la API, iniciando sesion primero para obtener el token:

```bash
API_URL="https://<api-id>.execute-api.us-east-1.amazonaws.com"

curl -i -X POST "$API_URL/servers" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"serverName": "servidor", "minecraftVersion": "1.21.10", "gameMode": "creative", "plan": "piedra", "maxPlayers": 5}'
```

Respuesta esperada (`202`):

```json
{
  "message": "Servidor en proceso de creacion.",
  "instanceId": "i-0123456789abcdef0",
  "plan": "piedra",
  "instanceType": "t3.small",
  "status": "creating"
}
```

Sin el header `Authorization` la respuesta es `401`. Cuando la instancia termina de instalarse, el servidor aparece en `GET /servers` con su IP publica y se conecta como se explica en el [README](README.md). Los logs de la funcion se ven con:

```bash
aws logs tail /aws/lambda/create_server --since 10m
```

Pruebas en `tests/test_create_server_planes.py` y `tests/test_create_server_comandos.py`.

## 2. Lambda `register_user`

Codigo: [`lambdas/register_user/lambda_function.py`](lambdas/register_user/lambda_function.py)

Recibe `username`, `email` y `password`. Valida los datos, crea la tabla `users` si no existe y guarda al usuario. La contrasena no se guarda en texto plano: se guarda un hash PBKDF2-SHA256 con un salt aleatorio (modulo `hashlib` de Python). Las credenciales de RDS se leen de Secrets Manager en tiempo de ejecucion, igual que en las Lambdas de los flujos 2 y 3.

| Respuesta | Cuando                                                                                |
| --------- | ------------------------------------------------------------------------------------- |
| 201       | Usuario creado, regresa `userId`                                                      |
| 400       | Datos invalidos (username de 3 a 30 caracteres, email valido, password de al menos 8) |
| 409       | El username o el email ya existen                                                     |
| 500       | Error interno (detalle en CloudWatch Logs)                                            |

`deploy.sh` despliega todas las carpetas de `lambdas/`, incluida esta:

```bash
./scripts/deploy.sh
```

Probar el registro:

```bash
API_URL="https://<api-id>.execute-api.us-east-1.amazonaws.com"

curl -i -X POST "$API_URL/register" \
  -H "Content-Type: application/json" \
  -d '{ "username": "user1", "email": "email@email.com", "password": "minecraft123"}'
```

Respuesta esperada:

```json
{
  "message": "Usuario registrado",
  "userId": "1",
  "username": "user1"
}
```

Si se corre otra vez con los mismos datos, la respuesta es `409`.

Los logs de la funcion se ven con:

```bash
aws logs tail /aws/lambda/register_user --since 10m
```

## 3. Lambda `login_user`

Codigo: [`lambdas/login_user/lambda_function.py`](lambdas/login_user/lambda_function.py)

Recibe `email` y `password`, busca al usuario en la tabla `users` y compara la password con el hash guardado (`hmac.compare_digest`, para que la comparacion tarde lo mismo aunque no coincida). Si el email no existe o la password es incorrecta regresa el mismo mensaje, asi no se puede saber que cuentas estan registradas.

| Respuesta | Cuando                                                |
| --------- | ----------------------------------------------------- |
| 200       | Credenciales correctas, regresa `userId` y `username` |
| 400       | Falta el email o la password                          |
| 401       | Email o password incorrectos                          |
| 500       | Error interno                                         |

Se despliega con el mismo `./scripts/deploy.sh`. Probar el inicio de sesion con el usuario del paso anterior:

```bash
API_URL="https://<api-id>.execute-api.us-east-1.amazonaws.com"

curl -i -X POST "$API_URL/login" \
  -H "Content-Type: application/json" \
  -d '{ "email": "email@email.com", "password": "minecraft123" }'
```

Respuesta esperada:

```json
{
  "message": "Inicio de sesion correcto",
  "userId": "1",
  "username": "user1",
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIiwidXNlcm5hbWUiOiJ1c2VyMSIsImlhdCI6MTc5NTc5OTA0OSwiZXhwIjoxNzk1ODQyMjQ5fQ.uSZYn0rD6BQ65TXCQCM0iloHi3BnSjnU1JMWVCs9D3w"
}
```

Con una password incorrecta la respuesta es `401`.

## 4. Lambda `list_servers`

Codigo: [`lambdas/list_servers/lambda_function.py`](lambdas/list_servers/lambda_function.py)

Recibe `userId` y regresa los servidores de ese usuario, que lee de la tabla `servers` que crea la Lambda `create_server` (flujo 2). Solo lee esa tabla, no la modifica.

El estado y la IP publica se consultan en EC2 en cada llamada (`describe_instances`) y no se toman de la base de datos: en el Learner Lab las instancias se detienen al terminar la sesion y al encenderlas reciben una IP publica nueva, asi que la IP guardada dejaria de servir. Si una instancia ya no existe en EC2 se reporta como `terminated`.

| Respuesta | Cuando                                                               |
| --------- | -------------------------------------------------------------------- |
| 200       | Regresa la lista `servers` (vacia si el usuario no tiene servidores) |
| 400       | Falta el `userId`                                                    |
| 500       | Error interno                                                        |

Cada servidor de la lista tiene `serverId`, `serverName`, `status` (estado actual en EC2), `dbStatus` (estado guardado en RDS), `publicIp`, `address` (IP y puerto 25565 para pegar en Minecraft), `instanceType` y `createdAt`.

Se despliega con `./scripts/deploy.sh`. Probar con el `userId` que regreso el registro:

```bash
aws lambda invoke --function-name list_servers \
  --payload '{ "userId": "1" }' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Si el usuario aun no tiene servidores, la respuesta es:

```json
{
  "statusCode": 200,
  "headers": { "Content-Type": "application/json" },
  "body": "{\"userId\": \"1\", \"servers\": []}"
}
```

Al crear un servidor con `create_server` usando el mismo `userId`, aparece en esta lista con su estado y su IP.

## 5. Frontend

Codigo: [`frontend/`](frontend/)

Sitio estatico en HTML, CSS y JavaScript sin framework. La pagina de inicio es `frontend/index.html` (sus estilos, script e imagenes estan en `landing/`) para que se abra en la raiz del sitio sin `/landing/` en la URL. Las demas pantallas tienen su carpeta (`login/`, `registro/`, `servidores/`, `nuevo-servidor/`, `servidor/`) con su HTML, CSS y JS, y lo compartido esta en [`frontend/recursos-compartidos/`](frontend/recursos-compartidos/). La URL del API Gateway va en [`frontend/recursos-compartidos/config.js`](frontend/recursos-compartidos/config.js); mientras esta vacia, el frontend usa datos de ejemplo.

Para probarlo en local, desde la raiz del repositorio:

```bash
cd frontend
python3 -m http.server 8000
```

y abrir http://localhost:8000 en el navegador. Hace falta un servidor (no abrir el `index.html` con doble clic) porque los archivos `.js` se cargan como modulos.

## 6. Rutas en el API Gateway

El API Gateway (`minecraft-servers-api`, HTTP API con CORS) lo crea `create_services.sh`. `deploy.sh` conecta cada Lambda con su ruta en la funcion `route_for`:

| Ruta                                | Lambda                      |
| ----------------------------------- | --------------------------- |
| `POST /servers`                     | `create_server`             |
| `POST /register`                    | `register_user`             |
| `POST /login`                       | `login_user`                |
| `GET /servers?userId=`              | `list_servers`              |
| `DELETE /servers?userId=&serverId=` | `delete_server` (seccion 9) |

`deploy.sh` tambien deja todas las Lambdas con 25 segundos de timeout (un poco menos que el maximo de 30 s de API Gateway, para que la Lambda alcance a responder o falle con un error legible en lugar de un 503), asi que ya no hace falta subirlo a mano como en las secciones anteriores. Al terminar imprime la URL de la API:

```bash
./scripts/deploy.sh
```

Probar las rutas con la URL que imprimio (sin `/` al final):

```bash
API_URL="https://<api-id>.execute-api.us-east-1.amazonaws.com"

curl -i -X POST "$API_URL/register" -H "Content-Type: application/json"   -d '{"username": "steve_builder", "email": "steve@correo.com", "password": "minecraft123"}'

curl -i -X POST "$API_URL/login" -H "Content-Type: application/json"   -d '{"email": "steve@correo.com", "password": "minecraft123"}'

curl -i "$API_URL/servers?userId=1"
```

Para probar el frontend en local contra la API real, se pone esa URL en [`frontend/recursos-compartidos/config.js`](frontend/recursos-compartidos/config.js) (sin hacer commit de ese cambio). Al publicarlo en S3 no hace falta: `deploy_frontend.sh` la pone solo (seccion 8).

## 7. Pruebas unitarias

Codigo: [`tests/`](tests/)

Pruebas con pytest de `register_user`, `login_user` y `list_servers`. No se conectan a AWS: la conexion a RDS y el cliente de EC2 se reemplazan con objetos falsos (`unittest.mock`), asi que corren en segundos y sin credenciales del Learner Lab.

| Archivo                          | Que prueba                                                                                                                                                                                             |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `test_register_user.py`          | Datos invalidos y JSON invalido (400), registro correcto (201), que la password se guarde con hash y el email en minusculas, usuario repetido (409) y error de base de datos (500)                     |
| `test_login_user.py`             | Faltan datos (400), login correcto (200), password incorrecta y email inexistente con el mismo mensaje (401), base de datos sin tabla `users` y hashes con formato desconocido                         |
| `test_list_servers.py`           | Sin `userId` (400), usuario sin servidores, estado e IP desde EC2, instancia que ya no existe (`terminated`), que solo consulte los servidores del usuario y error de EC2 (500)                        |
| `test_delete_server.py`          | Datos invalidos (400), borrado correcto (termina la instancia y quita el registro), servidor de otro usuario (404), instancia que ya no existe en EC2 y que no se borre el registro si falla EC2 (500) |
| `test_create_server_planes.py`   | Instancia y memoria de Java de cada plan, plan por defecto (Hierro), plan que no existe (400) y limite de jugadores de cada plan                                                                       |
| `test_create_server_comandos.py` | Servidor sin comandos, que con `opUsername` el script de la EC2 deje esa cuenta como operador antes de arrancar, y nombres de Minecraft invalidos (400)                                                |

Para correrlas, desde la raiz del repositorio:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r tests/requirements.txt
pytest tests -v
```

## 8. Publicar el frontend en S3

Script: [`scripts/deploy_frontend.sh`](scripts/deploy_frontend.sh)

Publica `frontend/` en un bucket de S3 configurado como sitio web estatico (como en la sesion 6):

1. Busca la URL del API Gateway `minecraft-servers-api`.
2. Crea el bucket `minecraft-frontend-<id de la cuenta>` si no existe y lo configura como sitio web con `index.html` como documento de indice, asi `/login/` abre `login/index.html`.
3. Da lectura publica a los archivos con una bucket policy (`s3:GetObject`) en lugar de ACLs.
4. Copia el frontend a una carpeta temporal, genera ahi `config.js` con la URL del API y lo sube con `aws s3 sync --delete`. El `config.js` del repo no se modifica.

Se corre despues de `deploy.sh`:

```bash
./scripts/deploy_frontend.sh
```

Al final imprime la direccion del sitio, por ejemplo `http://minecraft-frontend-<id de la cuenta>.s3-website-us-east-1.amazonaws.com`. Cada vez que cambia el frontend se vuelve a correr el mismo script.

## 9. Borrar servidores y comandos

### Lambda `delete_server`

Codigo: [`lambdas/delete_server/lambda_function.py`](lambdas/delete_server/lambda_function.py)

Recibe `userId` y `serverId` (ruta `DELETE /servers?userId=&serverId=`). Revisa en la tabla `servers` que el servidor sea de ese usuario, termina la instancia en EC2 (se pierden la maquina, el disco y el mundo) y borra el registro de la tabla para que ya no aparezca en la lista. Si la instancia ya no existia en EC2 solo se borra el registro. Si EC2 falla, el registro se queda para poder reintentar.

| Respuesta | Cuando                                                                    |
| --------- | ------------------------------------------------------------------------- |
| 200       | Servidor borrado                                                          |
| 400       | Falta el `userId` o el `serverId` no tiene formato de instancia (`i-...`) |
| 404       | El servidor no existe o es de otro usuario                                |
| 500       | Error interno                                                             |

En el frontend se borra desde el detalle del servidor, con una ventana de confirmacion.

```bash
curl -i -X DELETE "$API_URL/servers?userId=1&serverId=i-0123456789abcdef0"
```

### Comandos en el servidor

Al crear un servidor el usuario puede activar comandos y escribir su nombre de Minecraft Java. El frontend lo manda como `opUsername` a `create_server`, que lo valida (3 a 16 letras, numeros o `_`) y agrega al script de arranque de la EC2 la creacion de `ops.json` con esa cuenta como operador (nivel 4), usando la API de Mojang para obtener su UUID. Asi puede usar `/gamemode`, `/tp`, `/time` y dar permisos a otros con `/op`. Si la cuenta no existe, el servidor se instala igual pero sin operador.

## 10. Planes

Al crear un servidor el usuario elige un plan; `create_server` lo recibe como `plan` (si no viene, usa `hierro`) y con el decide el tipo de instancia, la memoria que se le da a Java (dejando espacio al sistema operativo) y el maximo de jugadores que acepta:

| Plan     | Instancia   | RAM  | Memoria de Java | Jugadores | Costo aprox. encendido |
| -------- | ----------- | ---- | --------------- | --------- | ---------------------- |
| Piedra   | `t3.small`  | 2 GB | 1 GB            | hasta 5   | $0.026/h               |
| Hierro   | `t3.medium` | 4 GB | 2.5 GB          | hasta 15  | $0.047/h               |
| Diamante | `t3.large`  | 8 GB | 6 GB            | hasta 30  | $0.088/h               |

El costo es el precio por hora de la instancia en us-east-1 (Linux, bajo demanda) mas la IP publica ($0.005/h). Con el servidor apagado solo se cobra el disco de 8 GB (menos de $1 al mes) hasta que se borra. Los limites de jugadores son conservadores: las tres instancias tienen 2 vCPU y el servidor de Minecraft usa sobre todo un nucleo, asi que lo que cambia entre planes es la memoria para cargar el mundo. El Learner Lab permite instancias hasta tamano `large`.

## 11. Encender y apagar servidores (`server_control`)

Codigo: [`lambdas/server_control/lambda_function.py`](lambdas/server_control/lambda_function.py)

Ruta `POST /servers/control` con body `{"serverId": "i-...", "action": "start" | "stop" | "status"}` y el token en `Authorization: Bearer <token>`. El dueno del servidor sale del token, igual que en `create_server` y `delete_server`. La Lambda revisa en la tabla `servers` que el servidor sea del usuario, consulta su estado en EC2 y llama a `start_instances` o `stop_instances`.

| Respuesta | Cuando                                                                         |
| --------- | ------------------------------------------------------------------------------ |
| 200       | Encendiendo, apagando, o ya estaba en ese estado (no se vuelve a llamar a EC2) |
| 400       | `action` o `serverId` invalidos                                                |
| 401       | Sin token, token invalido o vencido                                            |
| 404       | El servidor no existe o es de otro usuario                                     |
| 409       | El servidor esta cambiando de estado o ya no existe en EC2                     |
| 500       | Error interno                                                                  |

El respaldo del mundo (`backup`) queda desactivado: las EC2 no tienen rol de IAM ni AWS CLI para subir el mundo a S3. Sus funciones siguen en el codigo para cuando se agregue. Apagar no pierde el mundo: queda en el disco de la EC2.

Publica metricas en CloudWatch con el mismo patron que `create_server` y `delete_server` (peticiones por resultado, servidores encendidos y apagados, fallos de EC2 por accion y codigo de error, fallos de RDS, latencia). `monitoring.sh` crea sus alarmas y el dashboard `minecraft-server-control`. Pruebas en `tests/test_server_control.py`.

## 12. Monitoreo

Script: [`scripts/monitoring.sh`](scripts/monitoring.sh)

Crea las alarmas y los dashboards de CloudWatch de la plataforma. Es idempotente (si ya existen, los actualiza) y se corre despues de `create_services.sh`, porque necesita la API Gateway. El workflow `create.yml` tambien lo corre al montar la infraestructura:

```bash
./scripts/monitoring.sh
```

Al terminar quedan 33 alarmas y 4 dashboards.

### Dashboards

| Dashboard                  | Que muestra                                                                                                                                                                                              |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `minecraft-platform`       | Vista general: estado de todas las alarmas, CPU y fallas de estado de las EC2 de los servidores, API Gateway, vCPU en uso contra la cuota y RDS. Tiene enlaces a los otros tres                          |
| `minecraft-create-server`  | Peticiones por resultado, fallos por etapa, latencia, servidores creados por plan, fallos de `run_instances` por codigo de error y metricas de la Lambda                                                 |
| `minecraft-delete-server`  | Peticiones por resultado, fallos por etapa, latencia y metricas de la Lambda                                                                                                                             |
| `minecraft-server-control` | Peticiones por resultado, servidores encendidos y apagados, fallos por etapa, latencia, fallos de EC2 por accion y codigo de error, metricas de la Lambda y respaldos de mundos (desactivados por ahora) |

### Metricas

Las Lambdas publican sus propias metricas en el namespace `MinecraftPlatform` con `put_metric_data`. Cada una acumula las metricas durante la peticion (clase `Metrics`) y las publica en una sola llamada al terminar. Se necesitan metricas propias porque las Lambdas atrapan todas las excepciones y responden un 500 normal: para Lambda eso es una ejecucion exitosa y la metrica `Errors` de AWS no lo cuenta.

| Lambda           | Metricas                                                                                                                                                                                                                                                                                                                                                                               |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `create_server`  | `Requests`, `Success`, `ClientErrors`, `ServerErrors`, `AuthFailures`, `ValidationFailures`, `Ec2LaunchFailures` (dimension `ErrorCode`), `DbRegisterFailures`, `OrphanRollbacks`, `RollbackFailures`, `ServersCreated` (dimension `Plan`), `CreateLatency`, `Ec2LaunchLatency`, `DbRegisterLatency`                                                                                   |
| `delete_server`  | `DeleteRequests`, `ServersDeleted`, `DeleteClientErrors`, `DeleteServerErrors`, `DeleteAuthFailures`, `DeleteValidationFailures`, `DeleteNotFound`, `TerminateFailures` (dimension `ErrorCode`), `DeleteDbFailures` (dimension `Stage`), `DeleteInconsistentState`, `InstanceAlreadyGone`, `DeleteLatency`, `TerminateLatency`, `DbConnectLatency`                                     |
| `server_control` | `ControlRequests`, `ControlSuccess`, `ControlClientErrors`, `ControlServerErrors`, `ControlAuthFailures`, `ControlValidationFailures`, `ControlNotFound`, `ControlConflicts`, `Ec2ControlFailures` (dimensiones `Action` y `ErrorCode`), `ControlDbFailures` (dimension `Stage`), `ServersStarted`, `ServersStopped`, `ControlLatency`, `Ec2ControlLatency`, `ControlDbConnectLatency` |

Las Lambdas corren dentro de la VPC, asi que llegar a CloudWatch necesita el VPC endpoint `monitoring`, que crea `setup_network.sh`. Ademas, el cliente de CloudWatch de las Lambdas tiene timeouts de 2 segundos y no reintenta: si CloudWatch no responde, la peticion no se queda colgada hasta el timeout de la Lambda y solo queda `No se pudieron publicar metricas` en los logs.

### Alarmas

Todas evaluan periodos de 5 minutos y tratan la falta de datos como normal (sin datos la alarma no salta).

| Grupo            | Alarma                                                                                                               | Salta cuando                                                                                                                           |
| ---------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| Plataforma       | `minecraft-api-5xx`                                                                                                  | API Gateway devuelve algun 5xx                                                                                                         |
| Plataforma       | `minecraft-ec2-vcpu-quota`                                                                                           | Las vCPU en uso superan el 80% de `VCPU_QUOTA`                                                                                         |
| Plataforma       | `minecraft-rds-cpu-use`                                                                                              | La CPU de RDS pasa de 80%                                                                                                              |
| Plataforma       | `minecraft-rds-low-storage`                                                                                          | Quedan menos de 2 GB libres en RDS                                                                                                     |
| `create_server`  | `minecraft-lambda-create-server-errors`, `minecraft-lambda-create-throttles`                                         | Errores nativos de Lambda (timeout, memoria) o limitacion por concurrencia                                                             |
| `create_server`  | `minecraft-create-server-errors`                                                                                     | La Lambda devuelve algun 5xx                                                                                                           |
| `create_server`  | `minecraft-create-auth-fails`                                                                                        | 20 o mas respuestas 401 en 5 minutos (abuso o problema con el token)                                                                   |
| `create_server`  | `minecraft-create-rollback-failed`                                                                                   | **Critica**: no se pudo terminar una instancia tras fallar el registro en RDS, hay una EC2 huerfana cobrando                           |
| `create_server`  | `minecraft-create-ec2-fails`                                                                                         | `run_instances` fallo (capacidad, cuota de vCPU o throttling). El codigo de error se ve en el dashboard                                |
| `create_server`  | `minecraft-create-bd-fails`                                                                                          | No se pudo registrar el servidor en RDS (la instancia se termino por rollback)                                                         |
| `create_server`  | `minecraft-create-server-latency-p95`                                                                                | El percentil 95 de la creacion pasa de 10 s                                                                                            |
| `delete_server`  | `minecraft-lambda-delete-server-errors`, `minecraft-lambda-delete-throttles`, `minecraft-lambda-delete-duration-p99` | Errores nativos, limitacion por concurrencia, o p99 de duracion por encima del 80% del timeout                                         |
| `delete_server`  | `minecraft-delete-server-errors`                                                                                     | La Lambda devuelve algun 5xx                                                                                                           |
| `delete_server`  | `minecraft-delete-terminate-fails`                                                                                   | `terminate_instances` fallo. El registro se conserva y se puede reintentar                                                             |
| `delete_server`  | `minecraft-delete-db-fails`                                                                                          | Fallo una operacion contra RDS (conexion, consulta o borrado)                                                                          |
| `delete_server`  | `minecraft-delete-inconsistent-state`                                                                                | La EC2 se termino pero la fila sigue en `servers`: el usuario ve un servidor que ya no existe hasta que reintente                      |
| `delete_server`  | `minecraft-delete-server-latency-p95`                                                                                | El percentil 95 de la eliminacion pasa de 10 s                                                                                         |
| `delete_server`  | `minecraft-delete-auth-fails`                                                                                        | 20 o mas respuestas 401 en 5 minutos                                                                                                   |
| `delete_server`  | `minecraft-delete-not-found-spike`                                                                                   | 10 o mas respuestas 404 en 5 minutos: un usuario intenta borrar servidores que no son suyos, o un bug del frontend                     |
| `delete_server`  | `minecraft-delete-spike`                                                                                             | 10 o mas servidores eliminados en 5 minutos                                                                                            |
| `server_control` | `minecraft-lambda-errores`, `minecraft-lambda-control-throttles`, `minecraft-lambda-control-duration-p99`            | Errores nativos, limitacion por concurrencia, o p99 de duracion por encima del 80% del timeout                                         |
| `server_control` | `minecraft-control-server-errors`                                                                                    | La Lambda devuelve algun 5xx                                                                                                           |
| `server_control` | `minecraft-control-ec2-fails`                                                                                        | `start_instances` o `stop_instances` fallo. Se ven `Action` y `ErrorCode` en el dashboard                                              |
| `server_control` | `minecraft-control-db-fails`                                                                                         | Fallo la conexion o la consulta a RDS                                                                                                  |
| `server_control` | `minecraft-control-latency-p95`                                                                                      | El percentil 95 de encender o apagar pasa de 10 s                                                                                      |
| `server_control` | `minecraft-control-auth-fails`                                                                                       | 20 o mas respuestas 401 en 5 minutos                                                                                                   |
| `server_control` | `minecraft-control-conflicts-spike`                                                                                  | 10 o mas respuestas 409 en 5 minutos: usuarios pulsando encender o apagar mientras el servidor cambia de estado, o un bug del frontend |
| `server_control` | `minecraft-backups-fallidos`                                                                                         | Un respaldo de mundo fallo. Como el respaldo esta desactivado (seccion 11), la metrica `BackupsFailed` aun no se publica               |

### Notificaciones por correo

`create_services.sh` crea el topico SNS `minecraft-alerts`, y `monitoring.sh` lo conecta a todas las alarmas. Para recibir los avisos por correo, el script lee `ALERT_EMAIL` de un archivo `.env` en la raiz del repositorio (con `source`). `.env` esta en `.gitignore`, asi que el correo no se sube a GitHub; la plantilla es [`.env.example`](.env.example):

```bash
# editar .env y escribir el correo: ALERT_EMAIL=email@aqui.com
./scripts/create_services.sh
```

AWS manda un correo de confirmacion a esa direccion: hay que hacer clic en el enlace para activar las alertas. Si no existe `.env` o `ALERT_EMAIL` esta vacio, el topico se crea sin suscriptores, y las alarmas siguen visibles en CloudWatch y en los dashboards, solo que no avisan por correo. Una variable `ALERT_EMAIL` ya definida en el entorno (por ejemplo `ALERT_EMAIL=otro@email.com ./scripts/create_services.sh` o en los Secrets de Github) tiene prioridad sobre el `.env`. Si se cambia el correo y se vuelve a correr el script, se crea una suscripcion nueva y la anterior sigue hasta que se borre desde la consola de SNS.

### Variables opcionales de `monitoring.sh`

Se pasan al correr el script, por ejemplo `VCPU_QUOTA=18 ./scripts/monitoring.sh`. Solo se leen en ese momento: como el script es idempotente, si se vuelve a correr sin ellas las alarmas regresan a los valores por defecto.

| Variable                                                                                              | Por defecto                        | Para que sirve                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `SNS_TOPIC_ARN`                                                                                       | busca el topico `minecraft-alerts` | Topico al que notifican las alarmas. Si no existe, las alarmas se crean sin notificaciones                                                                                     |
| `SNS_TOPIC_NAME`                                                                                      | `minecraft-alerts`                 | Nombre del topico que se busca (debe coincidir con el de `create_services.sh` y `teardown.sh`)                                                                                 |
| `VCPU_QUOTA`                                                                                          | `18`                               | Tope de vCPU de la cuenta. En el Learner Lab son 9 instancias a la vez por 2 vCPU. En una cuenta normal, la cuota "Running On-Demand Standard instances" (codigo `L-1216C47A`) |
| `LAMBDA_TIMEOUT_MS`                                                                                   | `25000`                            | Timeout de las Lambdas (`TIMEOUT` de `deploy.sh`), para las alarmas de duracion                                                                                                |
| `CREATE_AUTH_FAILURES_THRESHOLD`, `DELETE_AUTH_FAILURES_THRESHOLD`, `CONTROL_AUTH_FAILURES_THRESHOLD` | `20`                               | Respuestas 401 en 5 minutos para que salte la alarma de cada Lambda                                                                                                            |
| `DELETE_NOT_FOUND_THRESHOLD`, `DELETE_SPIKE_THRESHOLD`, `CONTROL_CONFLICTS_THRESHOLD`                 | `10`                               | Respuestas 404 y eliminaciones (`delete_server`) y respuestas 409 (`server_control`) en 5 minutos                                                                              |

### Probar las alarmas

Forzar el estado de una alarma prueba que las notificaciones llegan (no prueba el umbral):

```bash
aws cloudwatch set-alarm-state --alarm-name minecraft-create-rollback-failed \
  --state-value ALARM --state-reason "prueba de notificacion"
```

Publicar un dato de prueba en una metrica propia prueba el umbral real, y tarda de 1 a 3 minutos en reflejarse (el dato queda en las graficas):

```bash
aws cloudwatch put-metric-data --namespace MinecraftPlatform \
  --metric-name AuthFailures --value 25 --unit Count
```

`teardown.sh` borra las alarmas y los dashboards y el topico SNS junto con sus suscripciones.
