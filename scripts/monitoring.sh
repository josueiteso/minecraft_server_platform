#!/bin/bash

# script que crea las alarmas y el dashboard de CloudWatch de la plataforma
# las metricas BackupsCompleted y BackupsFailed las publica la Lambda server_control

set -e

REGION="us-east-1"
FUNCTION_NAME="server_control"
NAMESPACE="MinecraftPlatform"
DASHBOARD_NAME="minecraft-platform"
SNS_TOPIC_ARN="${SNS_TOPIC_ARN:-}"

ALARM_ACTIONS=()
if [ -n "$SNS_TOPIC_ARN" ]; then
  ALARM_ACTIONS=(--alarm-actions "$SNS_TOPIC_ARN")
fi

echo "Creando alarma de errores en la Lambda $FUNCTION_NAME..."
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "minecraft-lambda-errores" \
  --alarm-description "La Lambda server_control registro al menos un error en 5 minutos" \
  --namespace "AWS/Lambda" --metric-name "Errors" \
  --dimensions Name=FunctionName,Value="$FUNCTION_NAME" \
  --statistic Sum --period 300 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching "${ALARM_ACTIONS[@]}"

echo "Creando alarma de respaldos fallidos..."
aws cloudwatch put-metric-alarm --region "$REGION" \
  --alarm-name "minecraft-backups-fallidos" \
  --alarm-description "Un respaldo de mundo fallo en los ultimos 5 minutos" \
  --namespace "$NAMESPACE" --metric-name "BackupsFailed" \
  --statistic Sum --period 300 --evaluation-periods 1 \
  --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold \
  --treat-missing-data notBreaching "${ALARM_ACTIONS[@]}"

echo "Creando dashboard $DASHBOARD_NAME..."
BODY_FILE=$(mktemp)
trap 'rm -f "$BODY_FILE"' EXIT

cat > "$BODY_FILE" << JSON
{
  "widgets": [
    {
      "type": "metric", "x": 0, "y": 0, "width": 12, "height": 6,
      "properties": {
        "title": "CPU de servidores Minecraft (%)",
        "region": "$REGION", "view": "timeSeries", "stat": "Average", "period": 300,
        "metrics": [[{"expression": "SEARCH('{AWS/EC2,InstanceId} MetricName=\"CPUUtilization\"', 'Average', 300)", "id": "cpu"}]]
      }
    },
    {
      "type": "metric", "x": 12, "y": 0, "width": 12, "height": 6,
      "properties": {
        "title": "Fallas de estado de EC2",
        "region": "$REGION", "view": "timeSeries", "stat": "Maximum", "period": 300,
        "metrics": [[{"expression": "SEARCH('{AWS/EC2,InstanceId} MetricName=\"StatusCheckFailed\"', 'Maximum', 300)", "id": "fallas"}]]
      }
    },
    {
      "type": "metric", "x": 0, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Errores y duracion de server_control",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["AWS/Lambda", "Errors", "FunctionName", "$FUNCTION_NAME", {"stat": "Sum", "label": "Errores"}],
          ["AWS/Lambda", "Duration", "FunctionName", "$FUNCTION_NAME", {"stat": "Average", "label": "Duracion promedio (ms)", "yAxis": "right"}]
        ]
      }
    },
    {
      "type": "metric", "x": 12, "y": 6, "width": 12, "height": 6,
      "properties": {
        "title": "Respaldos de mundos",
        "region": "$REGION", "view": "timeSeries", "period": 300,
        "metrics": [
          ["$NAMESPACE", "BackupsCompleted", {"stat": "Sum", "label": "Completados"}],
          ["$NAMESPACE", "BackupsFailed", {"stat": "Sum", "label": "Fallidos", "color": "#d62728"}]
        ]
      }
    },
    {
      "type": "alarm", "x": 0, "y": 12, "width": 24, "height": 3,
      "properties": {
        "title": "Estado de alarmas",
        "alarms": [
          "arn:aws:cloudwatch:$REGION:$(aws sts get-caller-identity --query Account --output text):alarm:minecraft-lambda-errores",
          "arn:aws:cloudwatch:$REGION:$(aws sts get-caller-identity --query Account --output text):alarm:minecraft-backups-fallidos"
        ]
      }
    }
  ]
}
JSON

aws cloudwatch put-dashboard --region "$REGION" \
  --dashboard-name "$DASHBOARD_NAME" --dashboard-body "file://$BODY_FILE" > /dev/null

echo "Listo. Alarmas y dashboard '$DASHBOARD_NAME' creados en $REGION."