#!/bin/bash
set -e

REGION="us-east-1"
CRAWLER_NAME="hardtech-db-extracts-crawler"
ATHENA_WORKGROUP="hardtech-analytics"
ATHENA_DB="hardtech_analytics"

echo "=========================================================="
echo "🚀 INICIANDO PIPELINE ANALÍTICO (INGESTA + GLUE + ATHENA)"
echo "=========================================================="

# ----------------------------------------------------
# 1. Detección Dinámica de Recursos S3 y API Gateway
# ----------------------------------------------------
echo "=== 1. DETECTANDO RECURSOS EN AWS ==="

# Resolver Bucket S3 dinámicamente
BUCKET_NAME=$(aws s3api list-buckets \
  --query "Buckets[?starts_with(Name, 'hardtechhub-bucket')].Name | [0]" \
  --output text \
  --region "$REGION")

if [ "$BUCKET_NAME" = "None" ] || [ -z "$BUCKET_NAME" ]; then
    echo "❌ Error: No se encontró ningún bucket con el prefijo 'hardtechhub-bucket'."
    exit 1
fi
echo "-> Bucket S3 detectado: $BUCKET_NAME"

# Resolver API Gateway URL dinámicamente
if [ -z "$API_URL" ]; then
    API_ID=$(aws apigatewayv2 get-apis --region "$REGION" \
      --query "Items[?Name=='HardTechHub-Gateway'].ApiId | [0]" \
      --output text 2>/dev/null || true)
      
    if [ -n "$API_ID" ] && [ "$API_ID" != "None" ]; then
        API_URL="https://${API_ID}.execute-api.${REGION}.amazonaws.com"
    else
        API_URL="https://otkv0558fh.execute-api.us-east-1.amazonaws.com"
    fi
fi
echo "-> API Gateway Endpoint objetivo: $API_URL"

# ----------------------------------------------------
# 2. Ingesta de Bases de Datos hacia S3
# ----------------------------------------------------
echo -e "\n=== 2. EJECUTANDO / VERIFICANDO INGESTA A S3 ==="
if [ -d "$HOME/hardtechhub-data-ingestion" ]; then
    echo "-> Reiniciando contenedores de extracción en MV Ingesta..."
    cd "$HOME/hardtechhub-data-ingestion"
    docker compose restart
    echo "-> Esperando 20 segundos a que finalice la carga de extractos a S3..."
    sleep 20
    cd - >/dev/null
fi

# Validar que los extractos existan en el bucket detectado
S3_FILES=$(aws s3 ls "s3://${BUCKET_NAME}/db-extracts/" --region "$REGION" 2>/dev/null | wc -l)
if [ "$S3_FILES" -eq 0 ]; then
    echo "❌ Error: No se encontraron extractos en s3://${BUCKET_NAME}/db-extracts/."
    exit 1
fi
echo "-> Extractos validados en S3 ($S3_FILES rutas/archivos listados)."

# ----------------------------------------------------
# 3. Validaciones de Entorno Glue / Athena
# ----------------------------------------------------
echo -e "\n=== 3. VALIDANDO ENTORNO ANALÍTICO ==="
if ! aws glue get-database --name "$ATHENA_DB" --region "$REGION" >/dev/null 2>&1; then
    echo "❌ Error: No existe la base Glue/Athena '$ATHENA_DB'."
    exit 1
fi

if ! aws athena get-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION" >/dev/null 2>&1; then
    echo "❌ Error: No existe el Workgroup '$ATHENA_WORKGROUP' en Athena."
    exit 1
fi
echo "-> Base Glue y Workgroup Athena validados correctamente."

# ----------------------------------------------------
# 4. Ejecución del Crawler de AWS Glue
# ----------------------------------------------------
echo -e "\n=== 4. EJECUTANDO CRAWLER DE AWS GLUE ==="
aws glue start-crawler --name "$CRAWLER_NAME" --region "$REGION" 2>/tmp/glue.err || {
    if grep -q "CrawlerRunningException" /tmp/glue.err; then
        echo "-> El Crawler ya se encontraba en ejecución."
    else
        cat /tmp/glue.err
        exit 1
    fi
}

echo "Esperando que el Crawler termine de catalogar..."
while true; do
    CRAWLER_STATE=$(aws glue get-crawler --name "$CRAWLER_NAME" --region "$REGION" --query "Crawler.State" --output text)
    if [ "$CRAWLER_STATE" == "READY" ]; then
        break
    fi
    echo "Estado del Crawler: $CRAWLER_STATE. Esperando 15s..."
    sleep 15
done

# Validar que el Crawler haya terminado con éxito
LAST_STATUS=$(aws glue get-crawler --name "$CRAWLER_NAME" --region "$REGION" --query "Crawler.LastCrawl.Status" --output text)
if [ "$LAST_STATUS" != "SUCCEEDED" ]; then
    echo "❌ Error: El Glue Crawler finalizó con estado '$LAST_STATUS'."
    exit 1
fi
echo "-> AWS Glue: Tablas catalogadas exitosamente."

# Pausa para propagación de catálogo a Athena
echo "Esperando 10s para sincronización completa del catálogo en Athena..."
sleep 10

# ----------------------------------------------------
# 5. Creación de Vistas en Amazon Athena
# ----------------------------------------------------
echo -e "\n=== 5. CREANDO VISTAS ANALÍTICAS EN ATHENA ==="
Q1="CREATE OR REPLACE VIEW vw_product_catalog AS 
    SELECT p.id AS product_id, p.name AS product_name, p.sku, p.price, p.stock, c.name AS category_name, b.name AS brand_name, p.created_at 
    FROM hardtech_analytics.products p 
    LEFT JOIN hardtech_analytics.categories c ON p.category_id = c.id 
    LEFT JOIN hardtech_analytics.brands b ON p.brand_id = b.id;"

Q2="CREATE OR REPLACE VIEW vw_category_brand_summary AS 
    SELECT c.name AS category_name, b.name AS brand_name, COUNT(p.id) AS total_products, ROUND(AVG(p.price), 2) AS avg_price, SUM(p.stock) AS total_stock 
    FROM hardtech_analytics.products p 
    LEFT JOIN hardtech_analytics.categories c ON p.category_id = c.id 
    LEFT JOIN hardtech_analytics.brands b ON p.brand_id = b.id 
    GROUP BY c.name, b.name;"

for QUERY in "$Q1" "$Q2"; do
    Q_ID=$(aws athena start-query-execution \
        --query-string "$QUERY" \
        --query-execution-context Database="$ATHENA_DB" \
        --work-group "$ATHENA_WORKGROUP" \
        --region "$REGION" \
        --query "QueryExecutionId" --output text)

    echo "-> Procesando vista ($Q_ID)..."
    
    while true; do
        ATHENA_STATUS=$(aws athena get-query-execution \
            --query-execution-id "$Q_ID" \
            --region "$REGION" \
            --query "QueryExecution.Status.State" --output text)

        if [ "$ATHENA_STATUS" == "SUCCEEDED" ]; then
            break
        fi

        if [ "$ATHENA_STATUS" == "FAILED" ] || [ "$ATHENA_STATUS" == "CANCELLED" ]; then
            REASON=$(aws athena get-query-execution --query-execution-id "$Q_ID" --region "$REGION" --query "QueryExecution.Status.StateChangeReason" --output text)
            echo "❌ Error: La consulta en Athena falló ($ATHENA_STATUS): $REASON"
            exit 1
        fi
        sleep 3
    done
done
echo "-> Amazon Athena: Vistas compiladas y verificadas."

# ----------------------------------------------------
# 6. Sincronización del Snapshot en Analytics Service
# ----------------------------------------------------
echo -e "\n=== 6. SINCRONIZANDO SNAPSHOT ANALÍTICO ==="
HTTP_CODE=$(curl -s -o /tmp/analytics_refresh.json -w "%{http_code}" -X POST "${API_URL}/api/analytics/refresh")

if [ "$HTTP_CODE" != "200" ]; then
    echo "❌ Error: Analytics Service respondió con HTTP $HTTP_CODE."
    cat /tmp/analytics_refresh.json
    echo ""
    exit 1
fi

echo "-> Snapshot analítico sincronizado con S3 exitosamente:"
cat /tmp/analytics_refresh.json
echo ""

echo "=========================================================="
echo "✅ PIPELINE ANALÍTICO COMPLETADO AL 100%"
echo "=========================================================="
