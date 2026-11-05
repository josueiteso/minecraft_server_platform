#!/bin/bash

# script que empaqueta y despliega las lambdas
set -e

REGION="us-east-1"
DB_INSTANCE_IDENTIFIER="minecraft-db-instance"
LAMBDA_SG_NAME="minecraft-lambda-sg"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/LabRole"
API_NAME="minecraft-servers-api"

# configuracion de todas las lambdas
RUNTIME="python3.12"
HANDLER="lambda_function.lambda_handler"
ENV_VARS="Variables={DB_INSTANCE_ID=${DB_INSTANCE_IDENTIFIER},SECURITY_GROUP_NAME=minecraft-servers-sg,INSTANCE_TYPE=t3.medium}"
TIMEOUT=30 # timeout de 30s es tambien el maximo de API Gateway
MEMORY_SIZE=256

# red: VPC de la RDS, SG de las lambdas y hasta 3 subnets
VPC_ID=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --region "$REGION" --query 'DBInstances[0].DBSubnetGroup.VpcId' --output text)
LAMBDA_SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$LAMBDA_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$LAMBDA_SG_ID" = "None" ]; then
  echo "No existe el SecurityGroup '$LAMBDA_SG_NAME'. Ejecuta primero ./setup_networks.sh"
  exit 1
fi
SUBNET_IDS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region "$REGION" \
  --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort -u -k1,1 | head -3 | awk '{print $2}' | paste -sd, -)
VPC_CONFIG="SubnetIds=${SUBNET_IDS},SecurityGroupIds=${LAMBDA_SG_ID}"

API_ID=$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  echo "No existe la API Gateway. Ejecuta primero create_services.sh"
  exit 1
fi

# para crear los endpoints para cada lambda
# ---AGREGAR LAS DEMAS LAMBDAS AQUI---
route_for() {
  case "$1" in
    create_server) echo "POST /servers" ;;
    *) echo "" ;;
  esac
}

if [ ! -d "$ROOT_DIR/lambdas" ]; then
  echo "No existe $ROOT_DIR/lambdas"
  exit 1
fi

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

COUNT=0
for DIR in "$ROOT_DIR"/lambdas/*/; do
  [ -f "$DIR/lambda_function.py" ] || continue
  NAME=$(basename "$DIR")
  COUNT=$((COUNT + 1))
  BUILD="$WORK_DIR/$NAME"
  ZIP="$WORK_DIR/$NAME.zip"

  echo "Desplegando '$NAME'..."

  # empaquetar en un zip
  mkdir -p "$BUILD"
  if [ -s "$DIR/requirements.txt" ]; then
    pip install -r "$DIR/requirements.txt" --target "$BUILD" \
      --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 \
      --only-binary=:all: --upgrade --quiet
  fi
  cp -r "$DIR"/. "$BUILD"/
  rm -rf "$BUILD/requirements.txt" "$BUILD/__pycache__"
  (cd "$BUILD" && zip -qr "$ZIP" .)

  # crear la funcion o si ya existe, actualizarla
  if aws lambda get-function --function-name "$NAME" --region "$REGION" > /dev/null 2>&1; then
    aws lambda update-function-code --function-name "$NAME" \
      --zip-file "fileb://$ZIP" --region "$REGION" > /dev/null
    aws lambda wait function-updated --function-name "$NAME" --region "$REGION"
    aws lambda update-function-configuration --function-name "$NAME" \
      --runtime "$RUNTIME" --handler "$HANDLER" \
      --timeout "$TIMEOUT" --memory-size "$MEMORY_SIZE" \
      --environment "$ENV_VARS" --vpc-config "$VPC_CONFIG" --region "$REGION" > /dev/null
    aws lambda wait function-updated --function-name "$NAME" --region "$REGION"
  else
    aws lambda create-function --function-name "$NAME" \
      --runtime "$RUNTIME" --role "$ROLE_ARN" --handler "$HANDLER" \
      --timeout "$TIMEOUT" --memory-size "$MEMORY_SIZE" \
      --zip-file "fileb://$ZIP" \
      --environment "$ENV_VARS" --vpc-config "$VPC_CONFIG" --region "$REGION" > /dev/null
    aws lambda wait function-active --function-name "$NAME" --region "$REGION"
  fi

  # conectar la lambda con API Gateway
  ROUTE_KEY=$(route_for "$NAME")
  if [ -n "$ROUTE_KEY" ]; then
    FUNC_ARN=$(aws lambda get-function --function-name "$NAME" --region "$REGION" \
      --query 'Configuration.FunctionArn' --output text)

    INTEGRATION_ID=$(aws apigatewayv2 get-integrations --api-id "$API_ID" --region "$REGION" \
      --query "Items[?IntegrationUri=='${FUNC_ARN}'].IntegrationId | [0]" --output text)
    if [ "$INTEGRATION_ID" = "None" ]; then
      INTEGRATION_ID=$(aws apigatewayv2 create-integration --api-id "$API_ID" --region "$REGION" \
        --integration-type AWS_PROXY --integration-uri "$FUNC_ARN" \
        --payload-format-version 2.0 --query 'IntegrationId' --output text)
    fi

    ROUTE_ID=$(aws apigatewayv2 get-routes --api-id "$API_ID" --region "$REGION" \
      --query "Items[?RouteKey=='${ROUTE_KEY}'].RouteId | [0]" --output text)
    if [ "$ROUTE_ID" = "None" ]; then
      aws apigatewayv2 create-route --api-id "$API_ID" --region "$REGION" \
        --route-key "$ROUTE_KEY" --target "integrations/${INTEGRATION_ID}" > /dev/null
    else
      aws apigatewayv2 update-route --api-id "$API_ID" --route-id "$ROUTE_ID" --region "$REGION" \
        --target "integrations/${INTEGRATION_ID}" > /dev/null
    fi

    # permiso para que API Gateway invoque la lambda
    aws lambda add-permission --function-name "$NAME" --region "$REGION" \
      --statement-id "apigw-${API_ID}" --action lambda:InvokeFunction \
      --principal apigateway.amazonaws.com \
      --source-arn "arn:aws:execute-api:${REGION}:${ACCOUNT_ID}:${API_ID}/*/*" \
      > /dev/null 2>&1 || true

    echo "  Ruta ${ROUTE_KEY} -> ${NAME}"
  fi
done

if [ "$COUNT" -eq 0 ]; then
  echo "No se encontro ninguna lambda (carpeta con lambda_function.py) en $ROOT_DIR/lambdas"
  exit 1
fi

echo "Listo. Se desplegaron $COUNT funciones lambda."
echo "API: https://${API_ID}.execute-api.${REGION}.amazonaws.com"
