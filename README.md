# minecraft_server_platform

Plataforma web basada en AWS que maneja la creacion, administracion y alojamiento de servidores de Minecraft Java Edition.

Asegurate de tener Minecraft Java instalado y AWS CLI configurado.

## Documentacion por flujo

- [Flujo 1: registro, inicio de sesion y panel de servidores](README_flujo1.md)

## Como crear el servidor de minecraft.

**1. Clona el repositoria y navega a su directorio**
```bash
https://github.com/josueiteso/minecraft_server_platform
cd minecraft_server_platform
```

**2. Monta la infraestructura**

Otorga permisos de ejecucion a los scripts:
```bash
chmod +x ./scripts/*.sh
```
Y ejecuta:
```bash
./scripts/create_services.sh
./scripts/setup_network.sh
./scripts/monitoring.sh
```

**3. Despliega la lambda y las rutas de la API Gateway**
```bash
./scripts/deploy.sh
```

Guarda la URL que se muestre para la API.

**4. Crea el servidor**

Desde Postman o cURL (en otra terminal) prueba crear un servidor:

```POST /servers```

Body esperado:

```
{
    "userId": <id_usuario>,
    "serverName": <nombre_del_servidor>,
    "minecraftVersion": <version_de_minecraft>,
    "gameMode": <modo_de_juego>,
    "maxPlayers": <num_jugadores>
}
```

Ejemplo:

```bash
curl -i -X POST "<API_URL>/servers" \
  -H "Content-Type: application/json" \
  -d '{
    "userId": "user123",
    "serverName": "soy un server",
    "minecraftVersion": "1.21.10",
    "gameMode": "creative",
    "maxPlayers": 5
  }'
```

Tu servidor deberia montarse. Espera a que la instancia de EC2 se incialize, puede tardar unos minutos.

**5. Conectate al servidor**

Abre Minecraft. Ve a Multiplayer -> Add Server. Pega la IP publica de la instancia de EC2 en Server Address y has click en "Done".

Listo. Ya puedes conectarte y jugar.

**6. Limpiar**

```bash
./scripts/teardown.sh
```
