# minecraft_server_platform

Plataforma web basada en AWS que maneja la creacion, administracion y alojamiento de servidores de Minecraft Java Edition.

Asegurate de tener Minecraft Java instalado y AWS CLI configurado.

# Fase 2

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
```

**3. Despliega la lambda**
```bash
./scripts/deploy.sh
```

**4. Invoca la lambda**

Aun no esta la API gateway, pero puedes invocar la funcion lambda con el comando ```aws lambda invoke```. Pasale los siguientes parametros:

userId (por ahora inventate uno)
serverName (nombre del servidor)
minecraftVersion (version de minecraft. ej: 1.21.10)
gameMode (survival, creative, adventure o spectator)
maxPlayers (de 1 a 100)

```aws lambda invoke --function-name create_server --payload '{ "userId": "<user id aqui>", "serverName": "<nombre del server aqui>", "minecraftVersion": "<version de minecraft aqui>", "gameMode": "<gamemode del mundo aqui>", "maxPlayers": <num de jugadores aqui> }' --cli-binary-format raw-in-base64-out /dev/stdout```

Ejemplo:

```aws lambda invoke --function-name create_server --payload '{ "userId": "user1", "serverName": "soy un server", "minecraftVersion": "1.21.10", "gameMode": "creative", "maxPlayers": 5 }' --cli-binary-format raw-in-base64-out /dev/stdout```

Tu servidor deberia montarse. Espera a que la instancia de EC2 se incialize, puede tardar unos minutos.

**5. Conectate al server**

Abre Minecraft. Ve a Multiplayer -> Add Server. Pega la IP publica de la instancia de EC2 en Server Address y has click en "Done".

Listo. Ya puedes conectarte y jugar.
