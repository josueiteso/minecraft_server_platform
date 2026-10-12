#!/bin/bash

# script que configura los security groups y bpc endpoints para que las lambdas puedan comunicarse con RDS

set -e

REGION="us-east-1"
DB_INSTANCE_IDENTIFIER="minecraft-db-instance"
LAMBDA_SG_NAME="minecraft-lambda-sg"

echo "Esperando a que RDS este disponible (puede tardar varios minutos)..."
aws rds wait db-instance-available --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" --region "$REGION"

VPC_ID=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --region "$REGION" --query 'DBInstances[0].DBSubnetGroup.VpcId' --output text)
RDS_SG_IDS=$(aws rds describe-db-instances --db-instance-identifier "$DB_INSTANCE_IDENTIFIER" \
  --region "$REGION" --query 'DBInstances[0].VpcSecurityGroups[].VpcSecurityGroupId' --output text)
echo "VPC: $VPC_ID | SG de RDS: $RDS_SG_IDS"

# hasta 3 subnets, una por cada availability zone
SUBNET_IDS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" --region "$REGION" \
  --query 'Subnets[].[AvailabilityZone,SubnetId]' --output text | sort -u -k1,1 | head -3 | awk '{print $2}' | paste -sd, -)
echo "Subnets: $SUBNET_IDS"

authorize() {
  local out
  if ! out=$(aws ec2 authorize-security-group-ingress --region "$REGION" "$@" 2>&1); then
    if echo "$out" | grep -q "Duplicate"; then echo "  (regla ya existía)"; else echo "$out"; return 1; fi
  fi
}

LAMBDA_SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$LAMBDA_SG_NAME" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [ "$LAMBDA_SG_ID" = "None" ]; then
  LAMBDA_SG_ID=$(aws ec2 create-security-group --group-name "$LAMBDA_SG_NAME" \
    --description "SG para Lambda de Minecraft y VPC endpoints" --vpc-id "$VPC_ID" \
    --region "$REGION" --query 'GroupId' --output text)
fi
echo "SG Lambda: $LAMBDA_SG_ID"

# todo lo que tenga el sg de la lambda puede comunicarse con RDS
for SG in $RDS_SG_IDS; do
  authorize --group-id "$SG" --protocol tcp --port 5432 --source-group "$LAMBDA_SG_ID"
done
authorize --group-id "$LAMBDA_SG_ID" --protocol tcp --port 443 --source-group "$LAMBDA_SG_ID" # este es el mismo puierto que usan los VPC endpoints

# creacion de VPC endpoints

# shellcheck disable=SC2086
for SVC in ec2 rds secretsmanager ssm; do
  EXISTING=$(aws ec2 describe-vpc-endpoints --region "$REGION" \
    --filters "Name=vpc-id,Values=$VPC_ID" "Name=service-name,Values=com.amazonaws.${REGION}.${SVC}" \
    --query 'VpcEndpoints[0].VpcEndpointId' --output text)
  if [ "$EXISTING" = "None" ]; then
    echo "Creando endpoint: $SVC"
    aws ec2 create-vpc-endpoint --region "$REGION" --vpc-id "$VPC_ID" \
      --vpc-endpoint-type Interface \
      --service-name "com.amazonaws.${REGION}.${SVC}" \
      --subnet-ids ${SUBNET_IDS//,/ } \
      --security-group-ids "$LAMBDA_SG_ID" \
      --private-dns-enabled > /dev/null
  else
    echo "Endpoint $SVC ya existe: $EXISTING"
  fi
done

echo "Listo. Los VPC endpoints, security groups y subnets para las lambdas estan listos."
