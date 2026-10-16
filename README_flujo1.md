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
