#!/bin/bash

# script que publica el frontend en un bucket de S3 configurado como sitio web estatico
set -e

REGION="us-east-1"
API_NAME="minecraft-servers-api"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
FRONTEND_BUCKET="minecraft-frontend-${ACCOUNT_ID}"

# la URL del API Gateway que crea create_services.sh
API_ID=$(aws apigatewayv2 get-apis --region "$REGION" \
  --query "Items[?Name=='${API_NAME}'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  echo "No existe la API Gateway. Ejecuta primero create_services.sh y deploy.sh"
  exit 1
fi
API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com"

# crea el bucket (si no existe)
if aws s3api head-bucket --bucket "$FRONTEND_BUCKET" 2>/dev/null; then
  echo "El bucket del frontend ${FRONTEND_BUCKET} ya existe."
else
  echo "Creando bucket del frontend ${FRONTEND_BUCKET}..."
  aws s3api create-bucket --bucket "$FRONTEND_BUCKET" --region "$REGION" > /dev/null
fi

# sitio web estatico: al entrar a una carpeta (/login/) se abre su index.html
aws s3api put-bucket-website --bucket "$FRONTEND_BUCKET" \
  --website-configuration '{"IndexDocument":{"Suffix":"index.html"}}'

# lectura publica solo de los archivos del sitio (bucket policy en lugar de ACLs)
aws s3api put-public-access-block --bucket "$FRONTEND_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=false,RestrictPublicBuckets=false
aws s3api put-bucket-policy --bucket "$FRONTEND_BUCKET" --policy "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [{
    \"Sid\": \"LecturaPublica\",
    \"Effect\": \"Allow\",
    \"Principal\": \"*\",
    \"Action\": \"s3:GetObject\",
    \"Resource\": \"arn:aws:s3:::${FRONTEND_BUCKET}/*\"
  }]
}"

# copia del frontend con config.js apuntando a la API; el config.js del repo no se modifica
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cp -r "$ROOT_DIR/frontend/." "$WORK_DIR/"
cat > "$WORK_DIR/recursos-compartidos/config.js" <<EOF
// generado por scripts/deploy_frontend.sh
export const API_URL = "${API_URL}";
EOF

# --delete borra del bucket los archivos que ya no existen en el frontend
aws s3 sync "$WORK_DIR" "s3://${FRONTEND_BUCKET}" --delete --region "$REGION" \
  --cache-control "no-cache" > /dev/null

echo "Listo. Frontend publicado con la API ${API_URL}"
echo "Sitio web: http://${FRONTEND_BUCKET}.s3-website-${REGION}.amazonaws.com"
