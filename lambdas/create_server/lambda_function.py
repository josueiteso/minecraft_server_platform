import base64
import json
import os
import re
from http import HTTPStatus

import boto3
import psycopg2

REGION = "us-east-1"
ec2_client = boto3.client("ec2", region_name=REGION)
rds_client = boto3.client("rds", region_name=REGION)
secrets_client = boto3.client("secretsmanager", region_name=REGION)

DB_INSTANCE_ID = os.environ.get("DB_INSTANCE_ID", "minecraft-db-instance")
SECURITY_GROUP_NAME = os.environ.get("SECURITY_GROUP_NAME", "minecraft-servers-sg")

# planes: tipo de instancia, memoria para Java (deja espacio al sistema) y maximo de jugadores.
# el maximo es lo que aguanta cada maquina sin lag en un servidor vanilla con 2 vCPU
PLANS = {
    "piedra": {"instanceType": "t3.small", "memory": "1G", "maxPlayers": 5},
    "hierro": {"instanceType": "t3.medium", "memory": "2560M", "maxPlayers": 15},
    "diamante": {"instanceType": "t3.large", "memory": "6G", "maxPlayers": 30},
}

VALID_GAME_MODES = {"survival", "creative", "adventure", "spectator"}
# nombre de cuenta de Minecraft Java: 3 a 16 letras, numeros o _
MC_USERNAME_RE = re.compile(r"[A-Za-z0-9_]{3,16}")

# si el usuario activa comandos, su cuenta queda como operador (op) del servidor.
# ops.json necesita el UUID de la cuenta, que se busca en la API de Mojang al instalar;
# si la busqueda falla el servidor se instala igual, solo sin operador
OPS_SCRIPT = """
echo "enable-command-block=true" >> server.properties
OP_UUID=$(curl -fsS "https://api.mojang.com/users/profiles/minecraft/__OP__" | python3 -c "
import sys, json
i = json.load(sys.stdin)['id']
print('-'.join([i[:8], i[8:12], i[12:16], i[16:20], i[20:]]))
") || OP_UUID=""
if [ -n "$OP_UUID" ]; then
  echo "[{\\"uuid\\": \\"$OP_UUID\\", \\"name\\": \\"__OP__\\", \\"level\\": 4, \\"bypassesPlayerLimit\\": false}]" > ops.json
fi
"""

HEADERS = {"Content-Type": "application/json"}


def get_security_group_id():
    groups = ec2_client.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SECURITY_GROUP_NAME]}]
    )["SecurityGroups"]
    if not groups:
        raise RuntimeError(f"No existe el SecurityGroup '{SECURITY_GROUP_NAME}'")
    return groups[0]["GroupId"]


# carga las credenciales de RDS desde secrets manager
def get_rds_credentials():
    db_info = rds_client.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
    instance = db_info["DBInstances"][0]
    secret = json.loads(
        secrets_client.get_secret_value(
            SecretId=instance["MasterUserSecret"]["SecretArn"]
        )["SecretString"]
    )
    return {
        "host": instance["Endpoint"]["Address"],
        "port": instance["Endpoint"]["Port"],
        "username": secret["username"],
        "password": secret["password"],
        "dbname": instance["DBName"],
    }


def response(status, body):
    return {
        "statusCode": status.value if isinstance(status, HTTPStatus) else status,
        "headers": HEADERS,
        "body": json.dumps(body),
    }


# devuelve el payload ya como dict
def parse_event(event):

    if "body" not in event:
        return event
    raw = event["body"] or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise TypeError("El body debe ser un objeto JSON")
    return payload


def register_server(user_id, instance_id, server_name):
    creds = get_rds_credentials()
    conn = psycopg2.connect(
        host=creds["host"],
        port=creds["port"],
        database=creds["dbname"],
        user=creds["username"],
        password=creds["password"],
        connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS servers (
                    id SERIAL PRIMARY KEY,
                    user_id VARCHAR(100) NOT NULL,
                    instance_id VARCHAR(50) UNIQUE NOT NULL,
                    server_name VARCHAR(100),
                    status VARCHAR(30),
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """)
            cur.execute(
                "INSERT INTO servers (user_id, instance_id, server_name, status) VALUES (%s, %s, %s, %s);",
                (user_id, instance_id, server_name, "creating"),
            )
        conn.commit()
    finally:
        conn.close()


def build_user_data(
    mc_version, game_mode, max_players, server_name, op_username=None, memory="2G"
):
    ops = OPS_SCRIPT.replace("__OP__", op_username) if op_username else ""
    return f"""#!/bin/bash
set -e

apt-get update -y
apt-get install -y openjdk-21-jre-headless curl python3

useradd -m -r -s /bin/bash minecraft || true
mkdir -p /minecraft
cd /minecraft

MANIFEST_URL="https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

VERSION_URL=$(curl -fsS "$MANIFEST_URL" | python3 -c "
import sys, json
data = json.load(sys.stdin)
v = '{mc_version}'
if v == 'latest':
    v = data['latest']['release']
for entry in data['versions']:
    if entry['id'] == v:
        print(entry['url'])
        break
")

JAR_URL=$(curl -fsS "$VERSION_URL" | python3 -c "import sys,json; print(json.load(sys.stdin)['downloads']['server']['url'])")

curl -fL -o server.jar "$JAR_URL"

echo "eula=true" > eula.txt
cat > server.properties << PROPS
gamemode={game_mode}
max-players={max_players}
motd={server_name}
PROPS
{ops}

chown -R minecraft:minecraft /minecraft

cat > /etc/systemd/system/minecraft.service << UNIT
[Unit]
Description=Minecraft Server
After=network.target

[Service]
User=minecraft
WorkingDirectory=/minecraft
ExecStart=/usr/bin/java -Xms{memory} -Xmx{memory} -jar server.jar nogui
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now minecraft
"""


def lambda_handler(event, context):
    try:
        try:
            payload = parse_event(event)
        except (ValueError, TypeError):  # JSONDecodeError hereda de ValueError
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "El body debe ser un JSON valido"}
            )

        user_id = payload.get("userId")
        server_name = str(payload.get("serverName", "mc-server"))
        mc_version = str(payload.get("minecraftVersion", "1.21.10"))
        game_mode = str(payload.get("gameMode", "survival")).lower()
        max_players = payload.get("maxPlayers", 10)
        plan_name = str(payload.get("plan", "hierro")).lower()
        # opcional: con opUsername el servidor se crea con comandos activados para esa cuenta
        op_username = str(payload.get("opUsername") or "").strip()

        # validacion de parametros
        if not user_id:
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "El campo userId es requerido"}
            )
        if not re.fullmatch(r"\d+\.\d+(\.\d+)?", mc_version):
            return response(
                HTTPStatus.BAD_REQUEST, {"error": "minecraftVersion invalida"}
            )
        if game_mode not in VALID_GAME_MODES:
            return response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"gameMode debe ser uno de {sorted(VALID_GAME_MODES)}"},
            )
        plan = PLANS.get(plan_name)
        if plan is None:
            return response(
                HTTPStatus.BAD_REQUEST,
                {"error": f"plan debe ser uno de {sorted(PLANS)}"},
            )
        try:
            max_players = int(max_players)
            if not 1 <= max_players <= plan["maxPlayers"]:
                raise ValueError
        except (TypeError, ValueError):
            return response(
                HTTPStatus.BAD_REQUEST,
                {
                    "error": f"El plan {plan_name} acepta entre 1 y "
                    f"{plan['maxPlayers']} jugadores"
                },
            )
        if op_username and not MC_USERNAME_RE.fullmatch(op_username):
            return response(HTTPStatus.BAD_REQUEST, {"error": "opUsername invalido"})
        server_name = re.sub(r"[^A-Za-z0-9 _\-]", "", server_name)[:50] or "mc-server"

        user_data = build_user_data(
            mc_version,
            game_mode,
            max_players,
            server_name,
            op_username or None,
            plan["memory"],
        )

        ec2_response = ec2_client.run_instances(
            ImageId="ami-0c7217cdde317cfec",  # ami de ubuntu server en us-east-1
            InstanceType=plan["instanceType"],
            MinCount=1,
            MaxCount=1,
            UserData=user_data,
            SecurityGroupIds=[get_security_group_id()],
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Name", "Value": server_name},
                        {"Key": "OwnerId", "Value": str(user_id)},
                        {"Key": "Plan", "Value": plan_name},
                    ],
                }
            ],
        )
        instance_id = ec2_response["Instances"][0]["InstanceId"]
        print(f"EC2 creada: {instance_id}")

        try:
            register_server(user_id, instance_id, server_name)
        except Exception as db_err:
            # evita instancias sin registro
            print(
                f"Fallo al registrar en base de datos, terminando {instance_id}: {db_err}"
            )
            ec2_client.terminate_instances(InstanceIds=[instance_id])
            raise

        return response(
            HTTPStatus.ACCEPTED,
            {
                "message": "Servidor en proceso de creacion.",
                "instanceId": instance_id,
                "plan": plan_name,
                "instanceType": plan["instanceType"],
                "status": "creating",
            },
        )

    except Exception as e:  # noqa: BLE001
        print(f"Error procesando solicitud: {type(e).__name__}: {e}")
        return response(
            HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal Server Error"}
        )
