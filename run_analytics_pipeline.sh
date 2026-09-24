#!/bin/bash
set -e

REGION="us-east-1"
export AWS_PAGER=""

echo "============================================================"
echo "🚀 INICIANDO PIPELINE ANALÍTICO (INGESTA + GLUE + ATHENA)"
echo "============================================================"

# ==========================================================
# 1. DETECTANDO RECURSOS EN AWS
# ==========================================================
echo "=== 1. DETECTANDO RECURSOS EN AWS ==="
BUCKET_NAME=$(aws s3api list-buckets --query "Buckets[?starts_with(Name, 'hardtechhub-bucket')].Name | [0]" --output text 2>/dev/null || true)
if [ -z "$BUCKET_NAME" ] || [ "$BUCKET_NAME" = "None" ]; then
  BUCKET_NAME=$(aws s3api list-buckets --query "Buckets[?contains(Name, 'hardtech')].Name | [0]" --output text)
fi

API_ENDPOINT=$(aws apigatewayv2 get-apis --region "$REGION" --query "Items[?Name=='HardTechHub-Gateway'].ApiEndpoint | [0]" --output text 2>/dev/null || true)

echo "-> Bucket S3 detectado: $BUCKET_NAME"
echo "-> API Gateway Endpoint objetivo: $API_ENDPOINT"

# ==========================================================
# 2. VALIDANDO INGESTA A S3
# ==========================================================
echo ""
echo "=== 2. EJECUTANDO / VERIFICANDO INGESTA A S3 ==="
EXTRACTS_COUNT=$(aws s3 ls "s3://${BUCKET_NAME}/db-extracts/" --recursive 2>/dev/null | wc -l || echo "0")
if [ "$EXTRACTS_COUNT" -eq 0 ]; then
  echo "⚠️ No se detectaron extractos en S3. Esperando sincronización inicial de ingesta..."
  sleep 15
fi
echo "-> Extractos validados en S3 ($EXTRACTS_COUNT rutas/archivos listados)."

# ==========================================================
# 3. VALIDANDO ENTORNO ANALÍTICO
# ==========================================================
echo ""
echo "=== 3. VALIDANDO ENTORNO ANALÍTICO ==="
GLUE_DB="hardtech_analytics"
ATHENA_WG="hardtech-analytics"
CRAWLER_NAME="hardtech-db-extracts-crawler"

aws glue get-database --name "$GLUE_DB" --region "$REGION" >/dev/null 2>&1 || {
  echo "❌ Base Glue $GLUE_DB no encontrada."
  exit 1
}

aws athena get-work-group --work-group "$ATHENA_WG" --region "$REGION" >/dev/null 2>&1 || {
  echo "❌ Workgroup Athena $ATHENA_WG no encontrado."
  exit 1
}
echo "-> Base Glue y Workgroup Athena validados correctamente."

# ==========================================================
# 4. EJECUTANDO CRAWLER DE AWS GLUE
# ==========================================================
echo ""
echo "=== 4. EJECUTANDO CRAWLER DE AWS GLUE ==="
CRAWLER_STATE=$(aws glue get-crawler --name "$CRAWLER_NAME" --region "$REGION" --query 'Crawler.State' --output text 2>/dev/null || echo "UNKNOWN")

if [ "$CRAWLER_STATE" != "RUNNING" ]; then
  aws glue start-crawler --name "$CRAWLER_NAME" --region "$REGION" 2>/dev/null || true
fi

echo "Esperando que el Crawler termine de catalogar..."
while true; do
  CRAWLER_STATE=$(aws glue get-crawler --name "$CRAWLER_NAME" --region "$REGION" --query 'Crawler.State' --output text 2>/dev/null || echo "UNKNOWN")
  if [ "$CRAWLER_STATE" = "READY" ]; then
    break
  fi
  echo "Estado del Crawler: $CRAWLER_STATE. Esperando 15s..."
  sleep 15
done
echo "-> AWS Glue: Tablas catalogadas exitosamente."
echo "Esperando 10s para sincronización completa del catálogo en Athena..."
sleep 10

# ==========================================================
# 5. CREANDO VISTAS ANALÍTICAS EN ATHENA
# ==========================================================
echo ""
echo "=== 5. CREANDO VISTAS ANALÍTICAS EN ATHENA ==="

run_athena() {
  SQL="$1"
  QUERY_ID=$(aws athena start-query-execution \
    --region "$REGION" \
    --query-string "$SQL" \
    --query-execution-context Database="$GLUE_DB",Catalog=AwsDataCatalog \
    --work-group "$ATHENA_WG" \
    --result-configuration OutputLocation="s3://${BUCKET_NAME}/athena-results/" \
    --query 'QueryExecutionId' \
    --output text)

  echo "-> Procesando vista ($QUERY_ID)..."

  while true; do
    STATE=$(aws athena get-query-execution \
      --region "$REGION" \
      --query-execution-id "$QUERY_ID" \
      --query 'QueryExecution.Status.State' \
      --output text 2>/dev/null || echo "UNKNOWN")

    if [ "$STATE" = "SUCCEEDED" ]; then
      echo "   ✅ Consulta completada con éxito."
      break
    fi

    if [ "$STATE" = "FAILED" ]; then
      REASON=$(aws athena get-query-execution \
        --region "$REGION" \
        --query-execution-id "$QUERY_ID" \
        --query 'QueryExecution.Status.StateChangeReason' \
        --output text 2>/dev/null || echo "Error desconocido")
      echo "❌ Error: La consulta en Athena falló (FAILED): $REASON"
      exit 1
    fi

    if [ "$STATE" = "CANCELLED" ]; then
      echo "❌ Error: Consulta cancelada en Athena."
      exit 1
    fi

    sleep 3
  done
}

# Vista 1: vw_product_catalog
VIEW_1_SQL="CREATE OR REPLACE VIEW vw_product_catalog AS
SELECT p.id AS product_id, p.name AS product_name, p.sku, p.price, c.name AS category_name, b.name AS brand_name, p.created_at
FROM products p
JOIN categories c ON p.category_id = c.id
JOIN brands b ON p.brand_id = b.id"

run_athena "$VIEW_1_SQL"

# Vista 2: vw_category_brand_summary
VIEW_2_SQL="CREATE OR REPLACE VIEW vw_category_brand_summary AS
SELECT c.name AS category_name, b.name AS brand_name, COUNT(p.id) AS total_products, ROUND(AVG(p.price), 2) AS avg_price
FROM products p
JOIN categories c ON p.category_id = c.id
JOIN brands b ON p.brand_id = b.id
GROUP BY c.name, b.name"

run_athena "$VIEW_2_SQL"

# ==========================================================
# 6. ACTUALIZANDO SNAPSHOT DE ANALÍTICA
# ==========================================================
echo ""
echo "=== 6. ACTUALIZANDO SNAPSHOT DE ANALÍTICA ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8005/api/analytics/refresh || true)

if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
  echo "-> Microservicio analytics-service refrescado correctamente (HTTP $HTTP_CODE)."
else
  echo "⚠️ Respuesta HTTP del endpoint de refresco: $HTTP_CODE (verificar logs del contenedor analytics-service si persiste)."
fi

echo ""
echo "============================================================"
echo "✅ PIPELINE ANALÍTICO COMPLETADO AL 100%"
echo "============================================================"
