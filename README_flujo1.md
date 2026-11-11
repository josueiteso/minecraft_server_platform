# Flujo 1: Registro, inicio de sesion y panel de servidores

Responsable: Victoria

El usuario se registra o inicia sesion, el backend valida sus datos, guarda su cuenta en RDS y le da acceso a su panel, donde ve la lista de sus servidores y su estado.

Aqui estan, en orden, todos los comandos que se usaron para montar este flujo. Todos los comandos de AWS CLI se corren en una terminal de Linux (WSL Ubuntu) con las credenciales del Learner Lab configuradas.

## 0. Requisitos

Credenciales del Learner Lab: en AWS Academy entra a *AWS Details > AWS CLI > Show* y pega el contenido en `~/.aws/credentials`. Las credenciales caducan cuando termina la sesion del lab (4 horas), hay que volver a copiarlas cada sesion.

Comprueba que la CLI tiene acceso a la cuenta:

```bash
aws sts get-caller-identity
aws configure get region   # debe decir us-east-1
```

`deploy.sh` usa `pip` y `zip` para empaquetar las Lambdas. En Ubuntu se instalan con:

```bash
sudo apt update
sudo apt install -y python3-pip zip
```

## 1. Infraestructura base del equipo

El flujo 1 usa la RDS y la red que crean los scripts del equipo. Desde la raiz del repositorio:

```bash
chmod +x ./scripts/*.sh
./scripts/create_services.sh   # security group, bucket de respaldos y RDS
./scripts/setup_network.sh     # espera la RDS, reglas de red y VPC endpoints
```

## 2. Lambda `register_user`

Codigo: [`lambdas/register_user/lambda_function.py`](lambdas/register_user/lambda_function.py)

Recibe `username`, `email` y `password`. Valida los datos, crea la tabla `users` si no existe y guarda al usuario. La contrasena no se guarda en texto plano: se guarda un hash PBKDF2-SHA256 con un salt aleatorio (modulo `hashlib` de Python). Las credenciales de RDS se leen de Secrets Manager en tiempo de ejecucion, igual que en las Lambdas de los flujos 2 y 3.

| Respuesta | Cuando |
|---|---|
| 201 | Usuario creado, regresa `userId` |
| 400 | Datos invalidos (username de 3 a 30 caracteres, email valido, password de al menos 8) |
| 409 | El username o el email ya existen |
| 500 | Error interno (detalle en CloudWatch Logs) |

`deploy.sh` despliega todas las carpetas de `lambdas/`, incluida esta:

```bash
./scripts/deploy.sh
```

Lambda tiene por defecto 3 segundos de timeout y 128 MB de memoria. Conectarse a RDS y calcular el hash tarda mas que eso, asi que se aumentan solo para esta funcion:

```bash
aws lambda update-function-configuration --function-name register_user \
  --timeout 15 --memory-size 512
aws lambda wait function-updated --function-name register_user
```

Probar el registro:

```bash
aws lambda invoke --function-name register_user \
  --payload '{ "username": "vicky", "email": "vicky@example.com", "password": "minecraft123" }' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Respuesta esperada:

```json
{"statusCode": 201, "headers": {"Content-Type": "application/json"}, "body": "{\"message\": \"Usuario registrado\", \"userId\": \"1\", \"username\": \"vicky\"}"}
```

Si se corre otra vez con los mismos datos, la respuesta es `409`.

Los logs de la funcion se ven con:

```bash
aws logs tail /aws/lambda/register_user --since 10m
```

## 3. Lambda `login_user`

Codigo: [`lambdas/login_user/lambda_function.py`](lambdas/login_user/lambda_function.py)

Recibe `email` y `password`, busca al usuario en la tabla `users` y compara la password con el hash guardado (`hmac.compare_digest`, para que la comparacion tarde lo mismo aunque no coincida). Si el email no existe o la password es incorrecta regresa el mismo mensaje, asi no se puede saber que cuentas estan registradas.

| Respuesta | Cuando |
|---|---|
| 200 | Credenciales correctas, regresa `userId` y `username` |
| 400 | Falta el email o la password |
| 401 | Email o password incorrectos |
| 500 | Error interno |

Se despliega con el mismo `./scripts/deploy.sh` y se le aumenta el timeout igual que a `register_user`:

```bash
aws lambda update-function-configuration --function-name login_user \
  --timeout 15 --memory-size 512
aws lambda wait function-updated --function-name login_user
```

Probar el inicio de sesion con el usuario del paso anterior:

```bash
aws lambda invoke --function-name login_user \
  --payload '{ "email": "vicky@example.com", "password": "minecraft123" }' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Respuesta esperada:

```json
{"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": "{\"message\": \"Inicio de sesion correcto\", \"userId\": \"1\", \"username\": \"vicky\"}"}
```

Con una password incorrecta la respuesta es `401`.

## 4. Lambda `list_servers`

Codigo: [`lambdas/list_servers/lambda_function.py`](lambdas/list_servers/lambda_function.py)

Recibe `userId` y regresa los servidores de ese usuario, que lee de la tabla `servers` que crea la Lambda `create_server` (flujo 2). Solo lee esa tabla, no la modifica.

El estado y la IP publica se consultan en EC2 en cada llamada (`describe_instances`) y no se toman de la base de datos: en el Learner Lab las instancias se detienen al terminar la sesion y al encenderlas reciben una IP publica nueva, asi que la IP guardada dejaria de servir. Si una instancia ya no existe en EC2 se reporta como `terminated`.

| Respuesta | Cuando |
|---|---|
| 200 | Regresa la lista `servers` (vacia si el usuario no tiene servidores) |
| 400 | Falta el `userId` |
| 500 | Error interno |

Cada servidor de la lista tiene `serverId`, `serverName`, `status` (estado actual en EC2), `dbStatus` (estado guardado en RDS), `publicIp`, `address` (IP y puerto 25565 para pegar en Minecraft), `instanceType` y `createdAt`.

Se despliega con `./scripts/deploy.sh` y se le aumenta el timeout:

```bash
aws lambda update-function-configuration --function-name list_servers \
  --timeout 15 --memory-size 512
aws lambda wait function-updated --function-name list_servers
```

Probar con el `userId` que regreso el registro:

```bash
aws lambda invoke --function-name list_servers \
  --payload '{ "userId": "1" }' \
  --cli-binary-format raw-in-base64-out /dev/stdout
```

Si el usuario aun no tiene servidores, la respuesta es:

```json
{"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": "{\"userId\": \"1\", \"servers\": []}"}
```

Al crear un servidor con `create_server` usando el mismo `userId` (ver el README), aparece en esta lista con su estado y su IP.

## 5. Frontend

Codigo: [`frontend/`](frontend/)

Sitio estatico en HTML, CSS y JavaScript sin framework. Cada pantalla tiene su carpeta (`landing/`, `login/`, `registro/`, `servidores/`, `nuevo-servidor/`, `servidor/`) con su HTML, CSS y JS, y lo compartido esta en [`frontend/recursos-compartidos/`](frontend/recursos-compartidos/). La URL del API Gateway va en [`frontend/recursos-compartidos/config.js`](frontend/recursos-compartidos/config.js); mientras esta vacia, el frontend usa datos de ejemplo.

Para probarlo en local, desde la raiz del repositorio:

```bash
cd frontend
python3 -m http.server 8000
```

y abrir http://localhost:8000 en el navegador. Hace falta un servidor (no abrir el `index.html` con doble clic) porque los archivos `.js` se cargan como modulos.

## 6. Rutas en el API Gateway

El API Gateway (`minecraft-servers-api`, HTTP API con CORS) lo crea `create_services.sh`. `deploy.sh` conecta cada Lambda con su ruta en la funcion `route_for`; las del flujo 1 son:

| Ruta | Lambda |
|---|---|
| `POST /register` | `register_user` |
| `POST /login` | `login_user` |
| `GET /servers?userId=` | `list_servers` |

`deploy.sh` tambien deja todas las Lambdas con 30 segundos de timeout (el maximo de API Gateway), asi que ya no hace falta subirlo a mano como en las secciones anteriores. Al terminar imprime la URL de la API:

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

| Archivo | Que prueba |
|---|---|
| `test_register_user.py` | Datos invalidos y JSON invalido (400), registro correcto (201), que la password se guarde con hash y el email en minusculas, usuario repetido (409) y error de base de datos (500) |
| `test_login_user.py` | Faltan datos (400), login correcto (200), password incorrecta y email inexistente con el mismo mensaje (401), base de datos sin tabla `users` y hashes con formato desconocido |
| `test_list_servers.py` | Sin `userId` (400), usuario sin servidores, estado e IP desde EC2, instancia que ya no existe (`terminated`), que solo consulte los servidores del usuario y error de EC2 (500) |

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
