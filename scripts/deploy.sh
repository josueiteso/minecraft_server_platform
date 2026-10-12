#!/bin/bash

# script que empaqueta y despliega las lambdas
set -e

REGION="us-east-1"
DB_INSTANCE_IDENTIFIER="minecraft-db-instance"
LAMBDA_SG_NAME="minecraft-lambda-sg"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/LabRole"

# configuracion de todas las lambdas
RUNTIME="python3.12"
HANDLER="lambda_function.lambda_handler"
ENV_VARS="Variables={DB_INSTANCE_ID=${DB_INSTANCE_IDENTIFIER},SECURITY_GROUP_NAME=minecraft-servers-sg,INSTANCE_TYPE=t3.medium}"

# red: VPC de la RDS, SG de las lambdas y hasta 3 subnets
VPC_ID=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --region "$REGION" --query 'DBInstances[0].DBSubnetGroup.VpcId' --output text)
LAMBDA_SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$LAMBDA_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$LAMBDA_SG_ID" = "None" ]; then
  echo "No existe el SecurityGroup '$LAMBDA_SG_NAME'. Ejecuta primero ./setup_network.sh"
  exit 1
fi
SUBNET_IDS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region "$REGION" \
  --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort -u -k1,1 | head -3 | awk '{print $2}' | paste -sd, -)
VPC_CONFIG="SubnetIds=${SUBNET_IDS},SecurityGroupIds=${LAMBDA_SG_ID}"

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
      --environment "$ENV_VARS" --vpc-config "$VPC_CONFIG" --region "$REGION" > /dev/null
    aws lambda wait function-updated --function-name "$NAME" --region "$REGION"
  else
    aws lambda create-function --function-name "$NAME" \
      --runtime "$RUNTIME" --role "$ROLE_ARN" --handler "$HANDLER" \
      --zip-file "fileb://$ZIP" \
      --environment "$ENV_VARS" --vpc-config "$VPC_CONFIG" --region "$REGION" > /dev/null
    aws lambda wait function-active --function-name "$NAME" --region "$REGION"
  fi
done

if [ "$COUNT" -eq 0 ]; then
  echo "No se encontro ninguna lambda (carpeta con lambda_function.py) en $ROOT_DIR/lambdas"
  exit 1
fi

echo "Listo. Se desplegaron $COUNT funciones lambda."
