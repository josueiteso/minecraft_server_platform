#!/bin/bash

# script que elimina la infraestructura creada por create_services.sh, setup_network.sh y monitoring.sh

REGION="us-east-1"
DB_INSTANCE_IDENTIFIER="minecraft-db-instance"
SG_NAME="minecraft-servers-sg"
LAMBDA_SG_NAME="minecraft-lambda-sg"
DASHBOARD_NAME="minecraft-platform"
ALARMS=("minecraft-lambda-errores" "minecraft-backups-fallidos")
API_NAME="minecraft-servers-api"
JWT_SECRET_NAME="minecraft-jwt-secret"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BACKUP_BUCKET="minecraft-backups-${ACCOUNT_ID}"

sg_id() {
  aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=$1" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null
}

LAMBDA_SG_ID=$(sg_id "$LAMBDA_SG_NAME")
SERVERS_SG_ID=$(sg_id "$SG_NAME")

# si hay varias api gateway con el mismo nombre, las elimina todas
API_IDS=$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId" --output text)

# lambdas que usan el SG de lambdas
LAMBDA_NAMES=""
if [ "$LAMBDA_SG_ID" != "None" ]; then
  LAMBDA_NAMES=$(aws lambda list-functions --region "$REGION" \
    --query "Functions[?VpcConfig.SecurityGroupIds && contains(VpcConfig.SecurityGroupIds, '$LAMBDA_SG_ID')].FunctionName" \
    --output text)
fi

# instancias EC2 creadas por la lambda
INSTANCE_IDS=""
if [ "$SERVERS_SG_ID" != "None" ]; then
  INSTANCE_IDS=$(aws ec2 describe-instances --region "$REGION" \
    --filters "Name=instance.group-id,Values=$SERVERS_SG_ID" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
fi

# describe-vpc-endpoints no soporta filtrar por group-id, asi que se filtra del lado del cliente
endpoints_of_lambda_sg() {
  aws ec2 describe-vpc-endpoints --region "$REGION" \
    --query "VpcEndpoints[?State!='deleted' && Groups[?GroupId=='$LAMBDA_SG_ID']].VpcEndpointId" \
    --output text
}

ENDPOINT_IDS=""
if [ "$LAMBDA_SG_ID" != "None" ]; then
  ENDPOINT_IDS=$(endpoints_of_lambda_sg)
fi

echo "Se buscaran y se eliminaran los recursos relevantes a la aplicacion en $REGION:"
echo "API Gateway: $API_NAME (${API_IDS:-ninguna})"
echo "Instancia RDS: $DB_INSTANCE_IDENTIFIER"
echo "Instancias EC2: ${INSTANCE_IDS:-(ninguna)}"
echo "Alarmas CloudWatch: ${ALARMS[*]}"
echo "Dashboard CloudWatch: $DASHBOARD_NAME"
echo "Funciones Lambda (y sus log groups): ${LAMBDA_NAMES:-(ninguna)}"
echo "Secreto de tokens: $JWT_SECRET_NAME"
echo "VPC endpoints: ${ENDPOINT_IDS:-(ninguno)}"
echo "Bucket S3 (con todo su contenido): $BACKUP_BUCKET"
echo "Security groups: $LAMBDA_SG_NAME ($LAMBDA_SG_ID), $SG_NAME ($SERVERS_SG_ID)"
echo "Eliminando..."
echo

# api gateway (tambien se borran sus rutas)
for ID in $API_IDS; do
  aws apigatewayv2 delete-api --api-id "$ID" --region "$REGION"
  echo "API Gateway $API_NAME ($ID) eliminada."
done

# rds
RDS_DELETING=false
if aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" --region "$REGION" >/dev/null 2>&1; then
  aws rds delete-db-instance --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
    --skip-final-snapshot --delete-automated-backups --region "$REGION" >/dev/null
  RDS_DELETING=true
fi

# espera a rds
if [ "$RDS_DELETING" = true ]; then
  echo "Esperando a que RDS se elimine (puede tardar varios minutos)..."
  aws rds wait db-instance-deleted --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" --region "$REGION"
  echo "Instancia RDS eliminada."
fi

# instancias ec2
if [ -n "$INSTANCE_IDS" ]; then
  # shellcheck disable=SC2086
  aws ec2 terminate-instances --region "$REGION" --instance-ids $INSTANCE_IDS >/dev/null
  echo "Esperando a que las instancias EC2 se terminen..."
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --region "$REGION" --instance-ids $INSTANCE_IDS
  echo "Instancias EC2 eliminadas."
fi

# cloudwatch
aws cloudwatch delete-alarms --region "$REGION" --alarm-names "${ALARMS[@]}"
aws cloudwatch delete-dashboards --region "$REGION" --dashboard-names "$DASHBOARD_NAME"
echo "Alarmas y dashboard eliminados."

# lambdas (y sus log groups)
for FN in $LAMBDA_NAMES; do
  aws lambda delete-function --function-name "$FN" --region "$REGION"
  aws logs delete-log-group --log-group-name "/aws/lambda/$FN" --region "$REGION" 2>/dev/null || true
  echo "Lambda $FN eliminada."
done

# secreto de los tokens jwt
if aws secretsmanager describe-secret --secret-id "$JWT_SECRET_NAME" --region "$REGION" >/dev/null 2>&1; then
  aws secretsmanager delete-secret --secret-id "$JWT_SECRET_NAME" \
    --force-delete-without-recovery --region "$REGION" >/dev/null
  echo "Secreto $JWT_SECRET_NAME eliminado."
fi

# vpc endpoints
if [ -n "$ENDPOINT_IDS" ]; then
  # shellcheck disable=SC2086
  aws ec2 delete-vpc-endpoints --region "$REGION" --vpc-endpoint-ids $ENDPOINT_IDS >/dev/null
  echo "Esperando a que los VPC endpoints se eliminen..."
  while [ -n "$(endpoints_of_lambda_sg)" ]; do
    sleep 10
  done
  echo "VPC endpoints eliminados."
fi

# s3
if aws s3api head-bucket --bucket "$BACKUP_BUCKET" 2>/dev/null; then
  aws s3 rb "s3://${BACKUP_BUCKET}" --force >/dev/null
  echo "Bucket $BACKUP_BUCKET eliminado."
fi

# security groups
if [ "$LAMBDA_SG_ID" != "None" ]; then
  # quita las reglas de otros SG (ej. el de RDS) que referencian al SG de la lambda
  for SG in $(aws ec2 describe-security-groups --region "$REGION" \
      --filters "Name=ip-permission.group-id,Values=$LAMBDA_SG_ID" \
      --query 'SecurityGroups[].GroupId' --output text); do
    for PORT in 5432 443; do
      aws ec2 revoke-security-group-ingress --region "$REGION" --group-id "$SG" \
        --protocol tcp --port "$PORT" --source-group "$LAMBDA_SG_ID" >/dev/null 2>&1 || true
    done
  done

  # las ENIs de las lambdas en VPC pueden tardar en liberarse, asi que se reintenta
  SG_DELETED=false
  for _ in $(seq 1 12); do
    if aws ec2 delete-security-group --region "$REGION" --group-id "$LAMBDA_SG_ID" >/dev/null 2>&1; then
      SG_DELETED=true
      break
    fi
    sleep 15
  done
  if [ "$SG_DELETED" = true ]; then
    echo "Security group $LAMBDA_SG_NAME eliminado."
  else
    echo "No se pudo eliminar $LAMBDA_SG_NAME (algo aun lo usa). Vuelve a ejecutar el script en unos minutos."
  fi
fi

if [ "$SERVERS_SG_ID" != "None" ]; then
  aws ec2 delete-security-group --region "$REGION" --group-id "$SERVERS_SG_ID" \
    && echo "Security group $SG_NAME eliminado." \
    || echo "No se pudo eliminar $SG_NAME, probablemente hay instancias EC2 que aun lo usan."
fi

echo
echo "Listo. Los recursos ya no existen."
