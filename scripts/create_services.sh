#!/bin/bash

# script que monta la infraestructura

set -e

REGION="us-east-1"
SG_NAME="minecraft-servers-sg"

# el nombre del bucket incluye el ID de la cuenta para que sea unico en S3
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
BACKUP_BUCKET="minecraft-backups-${ACCOUNT_ID}"

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

# crea el bucket de respaldos de mundos (si no existe)
if aws s3api head-bucket --bucket "${BACKUP_BUCKET}" 2>/dev/null; then
  echo "El bucket de respaldos ${BACKUP_BUCKET} ya existe."
else
  echo "Creando bucket de respaldos ${BACKUP_BUCKET}..."
  aws s3api create-bucket --bucket "${BACKUP_BUCKET}" --region "${REGION}" > /dev/null
  aws s3api put-public-access-block --bucket "${BACKUP_BUCKET}" \
    --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  echo "Bucket de respaldos creado: ${BACKUP_BUCKET}"
fi

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