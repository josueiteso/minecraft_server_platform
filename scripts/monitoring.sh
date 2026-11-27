#!/bin/bash

# script unico de monitoreo: crea todas las alarmas y dashboards de CloudWatch de la plataforma
# es idempotente: put-metric-alarm y put-dashboard actualizan si ya existen
#
# dashboards:
#   minecraft-platform        vista general: estado de todas las alarmas e infraestructura compartida
#   minecraft-create-server   create_server
#   minecraft-delete-server   delete_server
#   minecraft-server-control  server_control

set -e

REGION="us-east-1"
NAMESPACE="MinecraftPlatform"
API_NAME="minecraft-servers-api"
DB_INSTANCE_IDENTIFIER="minecraft-db-instance"
CONTROL_FUNCTION="server_control"
CREATE_FUNCTION="create_server"
DELETE_FUNCTION="delete_server"
DASHBOARD_PLATFORM="minecraft-platform"
DASHBOARD_CREATE="minecraft-create-server"
DASHBOARD_DELETE="minecraft-delete-server"
DASHBOARD_CONTROL="minecraft-server-control"
SNS_TOPIC_NAME="${SNS_TOPIC_NAME:-minecraft-alerts}"
VCPU_QUOTA="${VCPU_QUOTA:-18}"
LAMBDA_TIMEOUT_MS="${LAMBDA_TIMEOUT_MS:-25000}" # timeout de delete_server, para la alarma de duracion
# umbrales de las alarmas de abuso (por periodo de 5 minutos)
CREATE_AUTH_FAILURES_THRESHOLD="${CREATE_AUTH_FAILURES_THRESHOLD:-20}"
DELETE_SPIKE_THRESHOLD="${DELETE_SPIKE_THRESHOLD:-10}"
DELETE_NOT_FOUND_THRESHOLD="${DELETE_NOT_FOUND_THRESHOLD:-10}"
DELETE_AUTH_FAILURES_THRESHOLD="${DELETE_AUTH_FAILURES_THRESHOLD:-20}"
CONTROL_AUTH_FAILURES_THRESHOLD="${CONTROL_AUTH_FAILURES_THRESHOLD:-20}"
CONTROL_CONFLICTS_THRESHOLD="${CONTROL_CONFLICTS_THRESHOLD:-10}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

# topic SNS de las alarmas: SNS_TOPIC_ARN lo sobreescribe. si no, se busca el topic por nombre.
# sin email las alarmas se crean igual, pero no notifican a nadie
if [ -z "${SNS_TOPIC_ARN:-}" ]; then
  SNS_TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT_ID}:${SNS_TOPIC_NAME}"
  if ! aws sns get-topic-attributes --topic-arn "$SNS_TOPIC_ARN" --region "$REGION" > /dev/null 2>&1; then
    echo "AVISO: no existe el topic SNS ${SNS_TOPIC_NAME}. Las alarmas se crean sin notificaciones."
    SNS_TOPIC_ARN=""
  fi
fi

API_ID=$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  echo "ERROR: no existe el API ${API_NAME}. Ejecuta primero create_services.sh."
  exit 1
fi

ALARM_ACTIONS=()
if [ -n "$SNS_TOPIC_ARN" ]; then
  ALARM_ACTIONS=(--alarm-actions "$SNS_TOPIC_ARN")
fi

ALARM_NAMES=()

GTE="GreaterThanOrEqualToThreshold"
GT="GreaterThanThreshold"
LT="LessThanThreshold"

# alarm NOMBRE DESCRIPCION NAMESPACE METRICA ESTADISTICA UMBRAL OPERADOR [args extra de aws, ej. --dimensions]
# stat puede ser Sum/Average/Maximum/Minimum o un percentil (p95, p99)
alarm() {
  local name="$1" desc="$2" ns="$3" metric="$4" stat="$5" threshold="$6" op="$7"
  shift 7
  local stat_args=(--statistic "$stat")
  if [[ "$stat" == p* ]]; then
    stat_args=(--extended-statistic "$stat")
  fi
  echo "Creando alarma $name..."
  aws cloudwatch put-metric-alarm --region "$REGION" \
    --alarm-name "$name" --alarm-description "$desc" \
    --namespace "$ns" --metric-name "$metric" \
    "${stat_args[@]}" --period 300 --evaluation-periods 1 \
    --threshold "$threshold" --comparison-operator "$op" \
    --treat-missing-data notBreaching "${ALARM_ACTIONS[@]}" "$@"
  ALARM_NAMES+=("$name")
}

# convierte nombres de alarma en el arreglo JSON de ARNs que pide el widget de alarmas
alarms_json() {
  local out="" n
  for n in "$@"; do
    out+="\"arn:aws:cloudwatch:${REGION}:${ACCOUNT_ID}:alarm:${n}\","
  done
  echo "[${out%,}]"
}

# alarmas de la plataforma
alarm "minecraft-api-5xx" \
  "API Gateway devolvio HTTP 5xx (incluye fallos de integracion con las Lambdas)" \
  "AWS/ApiGateway" 5xx Sum 1 "$GTE" \
  --dimensions Name=ApiId,Value="$API_ID"

alarm "minecraft-ec2-vcpu-quota" \
  "Las vCPU On-Demand en uso superan el 80% de la cuota (${VCPU_QUOTA}). Pedir aumento antes de que run_instances falle" \
  "AWS/Usage" ResourceCount Maximum "$((VCPU_QUOTA * 80 / 100))" "$GT" \
  --dimensions Name=Type,Value=Resource Name=Resource,Value=vCPU Name=Service,Value=EC2 Name=Class,Value=Standard/OnDemand

alarm "minecraft-rds-cpu-use" \
  "CPU de RDS por encima de 80%" \
  "AWS/RDS" CPUUtilization Average 80 "$GT" \
  --dimensions Name=DBInstanceIdentifier,Value="$DB_INSTANCE_IDENTIFIER"

alarm "minecraft-rds-low-storage" \
  "Quedan menos de 2 GB libres en RDS" \
  "AWS/RDS" FreeStorageSpace Minimum 2147483648 "$LT" \
  --dimensions Name=DBInstanceIdentifier,Value="$DB_INSTANCE_IDENTIFIER"

END_PLATFORM=${#ALARM_NAMES[@]}

# alarmas relevantes para server_control
alarm "minecraft-lambda-errores" \
  "La Lambda server_control registro al menos un error en 5 minutos" \
  "AWS/Lambda" Errors Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$CONTROL_FUNCTION"

alarm "minecraft-backups-fallidos" \
  "Un respaldo de mundo fallo en los ultimos 5 minutos" \
  "$NAMESPACE" BackupsFailed Sum 1 "$GTE"

alarm "minecraft-lambda-control-throttles" \
  "server_control esta siendo limitada por concurrencia" \
  "AWS/Lambda" Throttles Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$CONTROL_FUNCTION"

alarm "minecraft-lambda-control-duration-p99" \
  "El percentil 99% de duracion supera el 80% del timeout de server_control (${LAMBDA_TIMEOUT_MS} ms)" \
  "AWS/Lambda" Duration p99 "$((LAMBDA_TIMEOUT_MS * 80 / 100))" "$GT" \
  --dimensions Name=FunctionName,Value="$CONTROL_FUNCTION"

alarm "minecraft-control-server-errors" \
  "server_control devolvio al menos un HTTP 5xx en 5 minutos" \
  "$NAMESPACE" ControlServerErrors Sum 1 "$GTE"

alarm "minecraft-control-ec2-fails" \
  "start_instances o stop_instances fallo (capacidad, cuota de vCPU o estado incorrecto). Ver Action y ErrorCode en el dashboard" \
  "$NAMESPACE" Ec2ControlFailures Sum 1 "$GTE"

alarm "minecraft-control-db-fails" \
  "Fallo la conexion o la consulta a RDS al encender o apagar un servidor. Ver Stage en los logs" \
  "$NAMESPACE" ControlDbFailures Sum 1 "$GTE"

alarm "minecraft-control-latency-p95" \
  "El percentil 95% de las operaciones de encender/apagar supera 10s (EC2, Secrets Manager o RDS lentos)" \
  "$NAMESPACE" ControlLatency p95 10000 "$GT"

alarm "minecraft-control-auth-fails" \
  "Muchos 401 en server_control en 5 minutos (>= ${CONTROL_AUTH_FAILURES_THRESHOLD}). Posible abuso, tokens falsificados o un problema con JWT_SECRET" \
  "$NAMESPACE" ControlAuthFailures Sum "$CONTROL_AUTH_FAILURES_THRESHOLD" "$GTE"

alarm "minecraft-control-conflicts-spike" \
  "Muchos 409 en server_control en 5 minutos (>= ${CONTROL_CONFLICTS_THRESHOLD}). Usuarios pulsando encender/apagar mientras el servidor cambia de estado, o un bug del frontend" \
  "$NAMESPACE" ControlConflicts Sum "$CONTROL_CONFLICTS_THRESHOLD" "$GTE"

END_CONTROL=${#ALARM_NAMES[@]}

# alarmas relevantes para create_server
alarm "minecraft-lambda-create-server-errors" \
  "Lambda Errors mayor a 0 en create_server. Puede ser timeout o una excepcion fuera del handler" \
  "AWS/Lambda" Errors Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$CREATE_FUNCTION"

alarm "minecraft-create-server-errors" \
  "create_server devolvio al menos un HTTP 5xx en 5 minutos" \
  "$NAMESPACE" ServerErrors Sum 1 "$GTE"

alarm "minecraft-create-auth-fails" \
  "Muchos 401 en create_server en 5 minutos (>= ${CREATE_AUTH_FAILURES_THRESHOLD}). Posible abuso/fuerza bruta o problema con el JWT_SECRET o la emision de tokens" \
  "$NAMESPACE" AuthFailures Sum "$CREATE_AUTH_FAILURES_THRESHOLD" "$GTE"

alarm "minecraft-create-rollback-failed" \
  "No se pudo terminar una instancia tras fallar el registro en BD. Hay una EC2 huerfana cobrando. Revisa los logs y terminala a mano" \
  "$NAMESPACE" RollbackFailures Sum 1 "$GTE"

alarm "minecraft-create-ec2-fails" \
  "run_instances fallo (capacidad, cuota de vCPU o throttling de EC2). Ver ErrorCode en el dashboard" \
  "$NAMESPACE" Ec2LaunchFailures Sum 1 "$GTE"

alarm "minecraft-create-bd-fails" \
  "No se pudo registrar un servidor en RDS (la instancia fue terminada por rollback)" \
  "$NAMESPACE" DbRegisterFailures Sum 1 "$GTE"

alarm "minecraft-create-server-latency-p95" \
  "El percentil 95% de las operaciones de creacion supera 10s (run_instances, Secrets Manager o RDS lentos)" \
  "$NAMESPACE" CreateLatency p95 10000 "$GT"

alarm "minecraft-lambda-create-throttles" \
  "create_server esta siendo limitada por concurrencia" \
  "AWS/Lambda" Throttles Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$CREATE_FUNCTION"

END_CREATE=${#ALARM_NAMES[@]}

# alarmas relevantes para delete_server
alarm "minecraft-lambda-delete-server-errors" \
  "Lambda Errors mayor a 0 en delete_server. Puede ser timeout o una excepcion fuera del handler" \
  "AWS/Lambda" Errors Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$DELETE_FUNCTION"

alarm "minecraft-lambda-delete-throttles" \
  "delete_server esta siendo limitada por concurrencia" \
  "AWS/Lambda" Throttles Sum 1 "$GTE" \
  --dimensions Name=FunctionName,Value="$DELETE_FUNCTION"

alarm "minecraft-lambda-delete-duration-p99" \
  "El percentil 99% de duracion supera el 80% del timeout de delete_server (${LAMBDA_TIMEOUT_MS} ms)" \
  "AWS/Lambda" Duration p99 "$((LAMBDA_TIMEOUT_MS * 80 / 100))" "$GT" \
  --dimensions Name=FunctionName,Value="$DELETE_FUNCTION"

alarm "minecraft-delete-server-errors" \
  "delete_server devolvio al menos un HTTP 5xx en 5 minutos" \
  "$NAMESPACE" DeleteServerErrors Sum 1 "$GTE"

alarm "minecraft-delete-terminate-fails" \
  "terminate_instances fallo al eliminar un servidor. El registro se conserva y se puede reintentar. Ver ErrorCode en el dashboard" \
  "$NAMESPACE" TerminateFailures Sum 1 "$GTE"

alarm "minecraft-delete-db-fails" \
  "Fallo una operacion contra RDS al eliminar un servidor (conexion, consulta o borrado). Ver Stage en los logs" \
  "$NAMESPACE" DeleteDbFailures Sum 1 "$GTE"

alarm "minecraft-delete-inconsistent-state" \
  "La EC2 fue terminada pero la fila sigue en la tabla servers: el usuario ve un servidor que ya no existe. Se corrige reintentando el delete" \
  "$NAMESPACE" DeleteInconsistentState Sum 1 "$GTE"

alarm "minecraft-delete-server-latency-p95" \
  "El percentil 95% de las operaciones de eliminacion supera 10s (RDS, Secrets Manager o terminate_instances lentos)" \
  "$NAMESPACE" DeleteLatency p95 10000 "$GT"

alarm "minecraft-delete-auth-fails" \
  "Muchos 401 en delete_server en 5 minutos (>= ${DELETE_AUTH_FAILURES_THRESHOLD}). Posible abuso, tokens falsificados o un problema con JWT_SECRET o la emision de tokens" \
  "$NAMESPACE" DeleteAuthFailures Sum "$DELETE_AUTH_FAILURES_THRESHOLD" "$GTE"

alarm "minecraft-delete-not-found-spike" \
  "Muchos 404 en delete_server en 5 minutos (>= ${DELETE_NOT_FOUND_THRESHOLD}). Un usuario autenticado esta intentando borrar serverIds que no le pertenecen, o puede ser un bug en el frontend" \
  "$NAMESPACE" DeleteNotFound Sum "$DELETE_NOT_FOUND_THRESHOLD" "$GTE"

alarm "minecraft-delete-spike" \
  "Eliminaciones masivas en 5 minutos (>= ${DELETE_SPIKE_THRESHOLD}). Posible abuso, un script descontrolado o un bug del cliente" \
  "$NAMESPACE" ServersDeleted Sum "$DELETE_SPIKE_THRESHOLD" "$GTE"

# arreglos JSON de ARNs. el de la plataforma lista todas, los demas, solo las alarmas de su seccion
ALL_ALARMS_JSON=$(alarms_json "${ALARM_NAMES[@]}")
CONTROL_ALARMS_JSON=$(alarms_json "${ALARM_NAMES[@]:$END_PLATFORM:$((END_CONTROL - END_PLATFORM))}")
CREATE_ALARMS_JSON=$(alarms_json "${ALARM_NAMES[@]:$END_CONTROL:$((END_CREATE - END_CONTROL))}")
DELETE_ALARMS_JSON=$(alarms_json "${ALARM_NAMES[@]:$END_CREATE}")

# dashboards
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT

put_dashboard() {
  echo "Creando dashboard $1..."
  aws cloudwatch put-dashboard --region "$REGION" \
    --dashboard-name "$1" --dashboard-body "file://$BODY_FILE" > /dev/null
}

CONSOLE_URL="https://console.aws.amazon.com/cloudwatch/home?region=${REGION}#dashboards:name="

# --- dashboard de plataforma ---
cat > "$BODY_FILE" << JSON
{
  "widgets": [
    {
      "type": "text", "x": 0, "y": 0, "width": 24, "height": 1,
      "properties": {"markdown": "Dashboards por servicio: [create_server](${CONSOLE_URL}${DASHBOARD_CREATE}) | [delete_server](${CONSOLE_URL}${DASHBOARD_DELETE}) | [server_control](${CONSOLE_URL}${DASHBOARD_CONTROL})"}
    },
    {
      "type": "alarm", "x": 0, "y": 1, "width": 24, "height": 8,
      "properties": {
        "title": "Estado de todas las alarmas",
        "alarms": $ALL_ALARMS_JSON
      }
    },
    {
      "type": "metric", "x": 0, "y": 9, "width": 12, "height": 6,
      "properties": {
        "title": "CPU de servidores Minecraft (%)",
        "region": "$REGION", "view": "timeSeries", "stat": "Average", "period": 300,
        "metrics": [[{"expression": "SEARCH('{AWS/EC2,InstanceId} MetricName=\"CPUUtilization\"', 'Average', 300)", "id": "cpu"}]]
      }
    },
    {
      "type": "metric", "x": 12, "y": 9, "width": 12, "height": 6,
      "properties": {
        "title": "Fallas de estado de EC2",
        "region": "$REGION", "view": "timeSeries", "stat": "Maximum", "period": 300,
        "metrics": [[{"expression": "SEARCH('{AWS/EC2,InstanceId} MetricName=\"StatusCheckFailed\"', 'Maximum', 300)", "id": "fallas"}]]
      }
    },
    {
      "type": "metric", "x": 0, "y": 15, "width": 8, "height": 6,
      "properties": {
        "title": "API Gateway",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["AWS/ApiGateway", "Count", "ApiId", "$API_ID", {"label": "Peticiones"}],
          ["AWS/ApiGateway", "4xx", "ApiId", "$API_ID", {"label": "4xx"}],
          ["AWS/ApiGateway", "5xx", "ApiId", "$API_ID", {"label": "5xx", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 8, "y": 15, "width": 8, "height": 6,
      "properties": {
        "title": "vCPU EC2 en uso vs cuota ($VCPU_QUOTA)",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Maximum",
        "metrics": [
          ["AWS/Usage", "ResourceCount", "Type", "Resource", "Resource", "vCPU", "Service", "EC2", "Class", "Standard/OnDemand", {"label": "vCPU en uso"}]
        ],
        "annotations": {"horizontal": [{"label": "Cuota", "value": $VCPU_QUOTA}]}
      }
    },
    {
      "type": "metric", "x": 16, "y": 15, "width": 8, "height": 6,
      "properties": {
        "title": "RDS",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", "$DB_INSTANCE_IDENTIFIER", {"stat": "Average", "label": "CPU (%)"}],
          ["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", "$DB_INSTANCE_IDENTIFIER", {"stat": "Maximum", "label": "Conexiones", "yAxis": "right"}]
        ]
      }
    }
  ]
}
JSON
put_dashboard "$DASHBOARD_PLATFORM"

# --- dashboard de server_control ---
cat > "$BODY_FILE" << JSON
{
  "widgets": [
    {
      "type": "alarm", "x": 0, "y": 0, "width": 24, "height": 4,
      "properties": {
        "title": "Alarmas de server_control",
        "alarms": $CONTROL_ALARMS_JSON
      }
    },
    {
      "type": "metric", "x": 0, "y": 4, "width": 12, "height": 6,
      "properties": {
        "title": "Peticiones de encender/apagar por resultado",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "ControlRequests", {"label": "Total"}],
          ["$NAMESPACE", "ControlSuccess", {"label": "Exitosas (200)"}],
          ["$NAMESPACE", "ControlClientErrors", {"label": "Errores del cliente (4xx)"}],
          ["$NAMESPACE", "ControlServerErrors", {"label": "Errores del servidor (5xx)", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 4, "width": 12, "height": 6,
      "properties": {
        "title": "Servidores encendidos y apagados",
        "region": "$REGION", "view": "timeSeries", "stacked": true, "period": 3600, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "ServersStarted", {"label": "Encendidos"}],
          ["$NAMESPACE", "ServersStopped", {"label": "Apagados"}]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 10, "width": 12, "height": 6,
      "properties": {
        "title": "Fallos de server_control por etapa",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "ControlAuthFailures", {"label": "Auth (401)"}],
          ["$NAMESPACE", "ControlValidationFailures", {"label": "Validacion (400)"}],
          ["$NAMESPACE", "ControlNotFound", {"label": "No encontrado (404)"}],
          ["$NAMESPACE", "ControlConflicts", {"label": "Estado incorrecto (409)"}],
          ["$NAMESPACE", "Ec2ControlFailures", {"label": "start/stop de EC2"}],
          ["$NAMESPACE", "ControlDbFailures", {"label": "RDS", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 10, "width": 12, "height": 6,
      "properties": {
        "title": "Latencia de encender/apagar (ms)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$NAMESPACE", "ControlLatency", {"stat": "p50", "label": "Total p50"}],
          ["$NAMESPACE", "ControlLatency", {"stat": "p95", "label": "Total p95"}],
          ["$NAMESPACE", "Ec2ControlLatency", {"stat": "Average", "label": "start/stop EC2 (prom)"}],
          ["$NAMESPACE", "ControlDbConnectLatency", {"stat": "Average", "label": "Conexion BD (prom)"}]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 16, "width": 12, "height": 6,
      "properties": {
        "title": "Fallos de EC2 por accion y codigo de error",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [[{"expression": "SEARCH('{$NAMESPACE,Action,ErrorCode} MetricName=\"Ec2ControlFailures\"', 'Sum', 300)", "id": "ctlerr"}]]
      }
    },
    {
      "type": "metric", "x": 12, "y": 16, "width": 12, "height": 6,
      "properties": {
        "title": "Lambda server_control (plataforma)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["AWS/Lambda", "Errors", "FunctionName", "$CONTROL_FUNCTION", {"stat": "Sum", "label": "Errores"}],
          ["AWS/Lambda", "Throttles", "FunctionName", "$CONTROL_FUNCTION", {"stat": "Sum", "label": "Throttles"}],
          ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", "$CONTROL_FUNCTION", {"stat": "Maximum", "label": "Concurrencia max"}],
          ["AWS/Lambda", "Duration", "FunctionName", "$CONTROL_FUNCTION", {"stat": "p99", "label": "Duracion p99 (ms)", "yAxis": "right"}]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 22, "width": 12, "height": 6,
      "properties": {
        "title": "Respaldos de mundos (desactivados por ahora)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$NAMESPACE", "BackupsCompleted", {"stat": "Sum", "label": "Completados"}],
          ["$NAMESPACE", "BackupsFailed", {"stat": "Sum", "label": "Fallidos", "color": "#d62728"}]
        ]
      }
    }
  ]
}
JSON
put_dashboard "$DASHBOARD_CONTROL"

# --- dashboard de create_server ---
cat > "$BODY_FILE" << JSON
{
  "widgets": [
    {
      "type": "alarm", "x": 0, "y": 0, "width": 24, "height": 5,
      "properties": {
        "title": "Alarmas de create_server",
        "alarms": $CREATE_ALARMS_JSON
      }
    },
    {
      "type": "metric", "x": 0, "y": 5, "width": 12, "height": 6,
      "properties": {
        "title": "Peticiones de creacion por resultado",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "Requests", {"label": "Total"}],
          ["$NAMESPACE", "Success", {"label": "Exitosas (202)"}],
          ["$NAMESPACE", "ClientErrors", {"label": "Errores del cliente (4xx)"}],
          ["$NAMESPACE", "ServerErrors", {"label": "Errores del servidor (5xx)", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 5, "width": 12, "height": 6,
      "properties": {
        "title": "Fallos de creacion por etapa",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "AuthFailures", {"label": "Auth (401)"}],
          ["$NAMESPACE", "ValidationFailures", {"label": "Validacion (400)"}],
          ["$NAMESPACE", "Ec2LaunchFailures", {"label": "run_instances"}],
          ["$NAMESPACE", "DbRegisterFailures", {"label": "Registro en BD"}],
          ["$NAMESPACE", "RollbackFailures", {"label": "Rollback fallido", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 11, "width": 12, "height": 6,
      "properties": {
        "title": "Latencia de creacion (ms)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$NAMESPACE", "CreateLatency", {"stat": "p50", "label": "Total p50"}],
          ["$NAMESPACE", "CreateLatency", {"stat": "p95", "label": "Total p95"}],
          ["$NAMESPACE", "CreateLatency", {"stat": "p99", "label": "Total p99"}],
          ["$NAMESPACE", "Ec2LaunchLatency", {"stat": "Average", "label": "run_instances (prom)"}],
          ["$NAMESPACE", "DbRegisterLatency", {"stat": "Average", "label": "Registro BD (prom)"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 11, "width": 12, "height": 6,
      "properties": {
        "title": "Servidores creados por plan",
        "region": "$REGION", "view": "timeSeries", "stacked": true, "period": 3600, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "ServersCreated", "Plan", "piedra"],
          ["$NAMESPACE", "ServersCreated", "Plan", "hierro"],
          ["$NAMESPACE", "ServersCreated", "Plan", "diamante"]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 17, "width": 12, "height": 6,
      "properties": {
        "title": "Fallos de run_instances por codigo de error",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [[{"expression": "SEARCH('{$NAMESPACE,ErrorCode} MetricName=\"Ec2LaunchFailures\"', 'Sum', 300)", "id": "ec2err"}]]
      }
    },
    {
      "type": "metric", "x": 12, "y": 17, "width": 12, "height": 6,
      "properties": {
        "title": "Lambda create_server (plataforma)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["AWS/Lambda", "Errors", "FunctionName", "$CREATE_FUNCTION", {"stat": "Sum", "label": "Errores"}],
          ["AWS/Lambda", "Throttles", "FunctionName", "$CREATE_FUNCTION", {"stat": "Sum", "label": "Throttles"}],
          ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", "$CREATE_FUNCTION", {"stat": "Maximum", "label": "Concurrencia max"}],
          ["AWS/Lambda", "Duration", "FunctionName", "$CREATE_FUNCTION", {"stat": "p99", "label": "Duracion p99 (ms)", "yAxis": "right"}]
        ]
      }
    }
  ]
}
JSON
put_dashboard "$DASHBOARD_CREATE"

# --- dashboard de delete_server ---
cat > "$BODY_FILE" << JSON
{
  "widgets": [
    {
      "type": "alarm", "x": 0, "y": 0, "width": 24, "height": 6,
      "properties": {
        "title": "Alarmas de delete_server",
        "alarms": $DELETE_ALARMS_JSON
      }
    },
    {
      "type": "metric", "x": 0, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Peticiones de eliminacion por resultado",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "DeleteRequests", {"label": "Total"}],
          ["$NAMESPACE", "ServersDeleted", {"label": "Eliminados (200)"}],
          ["$NAMESPACE", "DeleteClientErrors", {"label": "Errores del cliente (4xx)"}],
          ["$NAMESPACE", "DeleteServerErrors", {"label": "Errores del servidor (5xx)", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Fallos de eliminacion por etapa",
        "region": "$REGION", "view": "timeSeries", "period": 300, "stat": "Sum",
        "metrics": [
          ["$NAMESPACE", "DeleteAuthFailures", {"label": "Auth (401)"}],
          ["$NAMESPACE", "DeleteValidationFailures", {"label": "Validacion (400)"}],
          ["$NAMESPACE", "DeleteNotFound", {"label": "No encontrado (404)"}],
          ["$NAMESPACE", "TerminateFailures", {"label": "terminate_instances"}],
          ["$NAMESPACE", "DeleteDbFailures", {"label": "Operacion en BD"}],
          ["$NAMESPACE", "DeleteInconsistentState", {"label": "EC2 terminada, fila pendiente", "color": "#d62728"}],
          ["$NAMESPACE", "InstanceAlreadyGone", {"label": "Instancia ya inexistente (informativo)"}]
        ]
      }
    },
    {
      "type": "metric", "x": 0, "y": 12, "width": 12, "height": 6,
      "properties": {
        "title": "Latencia de eliminacion (ms)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$NAMESPACE", "DeleteLatency", {"stat": "p50", "label": "Total p50"}],
          ["$NAMESPACE", "DeleteLatency", {"stat": "p95", "label": "Total p95"}],
          ["$NAMESPACE", "DeleteLatency", {"stat": "p99", "label": "Total p99"}],
          ["$NAMESPACE", "TerminateLatency", {"stat": "Average", "label": "terminate_instances (prom)"}],
          ["$NAMESPACE", "DbConnectLatency", {"stat": "Average", "label": "Conexion BD (prom)"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 12, "width": 12, "height": 6,
      "properties": {
        "title": "Lambda delete_server (plataforma)",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["AWS/Lambda", "Errors", "FunctionName", "$DELETE_FUNCTION", {"stat": "Sum", "label": "Errores"}],
          ["AWS/Lambda", "Throttles", "FunctionName", "$DELETE_FUNCTION", {"stat": "Sum", "label": "Throttles"}],
          ["AWS/Lambda", "ConcurrentExecutions", "FunctionName", "$DELETE_FUNCTION", {"stat": "Maximum", "label": "Concurrencia max"}],
          ["AWS/Lambda", "Duration", "FunctionName", "$DELETE_FUNCTION", {"stat": "p99", "label": "Duracion p99 (ms)", "yAxis": "right"}]
        ]
      }
    }
  ]
}
JSON
put_dashboard "$DASHBOARD_DELETE"

echo "Listo. ${#ALARM_NAMES[@]} alarmas y 4 dashboards ('$DASHBOARD_PLATFORM', '$DASHBOARD_CREATE', '$DASHBOARD_DELETE', '$DASHBOARD_CONTROL') creados en $REGION."
