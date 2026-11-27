# minecraft_server_platform

Plataforma web basada en AWS que maneja la creacion, administracion y alojamiento de servidores de Minecraft Java Edition.

## Requisitos

1. Terminal Unix o Unix-like. Si usas Windows puedes usar WSL2 con Ubuntu o la distribucion de tu preferencia.

2. AWS CLI instalada y configurada en tu terminal.
   
    https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html

    https://docs.aws.amazon.com/cli/latest/userguide/getting-started-quickstart.html

   Tambien instala las utilidades necesarias: zip, curl,  jq.

4. Minecraft Java Edition.

## Documentacion por flujo

- [Flujo 1: registro, inicio de sesion y panel de servidores](README_flujo1.md)

## Quick start

**1. Clona el repositorio y navega a su directorio**
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

**3. Despliega la lambda, las rutas de la API Gateway y el frontend**
```bash
./scripts/deploy.sh
./scripts/deploy_frontend.sh
```

Listo. Ya puedes acceder al frontend mediante la URL dada por ```deploy_frontend.sh```. 

## Como conectarse al servidor.

Una ves hayas creado tu servidor y que este activo:

1. Abre Minecraft. Asegurate que la version de tu instalacion coincida con la del servidor que creaste.
2. Ve a Multiplayer -> Add Server.
3. Pega la IP del servidor en Server Address.
4. Has click en "Done".

Listo. Ya puedes conectarte y jugar.

## Limpiar recursos

Ejecuta

```bash
./scripts/teardown.sh
```
