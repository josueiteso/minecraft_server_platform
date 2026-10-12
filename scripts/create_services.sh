#!/bin/bash

# script que monta la infraestructura

set -e

REGION="us-east-1"
SG_NAME="minecraft-servers-sg"

echo "Creando security group ${SG_NAME}..."

# crea los security groups
SG_ID=$(aws ec2 create-security-group \
  --group-name ${SG_NAME} \
  --description "SecurityGroup para servidores de Minecraft" \
  --region ${REGION} \
  --query 'GroupId' --output text 2>/dev/null || \
  aws ec2 describe-security-groups --group-names ${SG_NAME} --region ${REGION} --query 'SecurityGroups[0].GroupId' --output text)

echo "Security Group ID: ${SG_ID}"

aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 25565 --cidr 0.0.0.0/0 --region ${REGION} 2>/dev/null || true # puerto de minecraft
aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 22 --cidr 0.0.0.0/0 --region ${REGION} 2>/dev/null || true # puerto de ssh

DB_INSTANCE_IDENTIFIER="minecraft-db-instance"

if aws rds describe-db-instances \
  --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
  --region "${REGION}" >/dev/null 2>&1; then

  echo "La instancia RDS ya existe."

else
# creaa la tabla RDS
  if aws rds create-db-instance \
    --db-instance-identifier "${DB_INSTANCE_IDENTIFIER}" \
    --db-name "minecraftdb" \
    --allocated-storage 20 \
    --db-instance-class db.t3.medium \
    --engine postgres \
    --master-username postgres \
    --manage-master-user-password \
    --no-publicly-accessible \
    --region "${REGION}"; then

    echo "Instancia de RDS creada correctamente. Puede tardar unos minutos en estar lista."

  else
    echo "ERROR: No se pudo crear la instancia RDS."
    exit 1
  fi

fi

echo "Servicios creados correctamente."
