import os
import json
import random
from datetime import datetime, timezone
import boto3

REGION = "us-east-1"
s3 = boto3.client("s3", region_name=REGION)

# ----------------------------------------------------
# 1. Detección Dinámica del Bucket S3
# ----------------------------------------------------
# Intenta leer de variable de entorno; si no existe, busca por prefijo
BUCKET_NAME = os.getenv("S3_BUCKET_NAME") or os.getenv("S3_BUCKET")

if not BUCKET_NAME:
    try:
        response = s3.list_buckets()
        matching_buckets = [
            b["Name"] for b in response.get("Buckets", [])
            if b["Name"].startswith("hardtechhub-bucket")
        ]
        if matching_buckets:
            BUCKET_NAME = matching_buckets[0]
        else:
            raise RuntimeError("No se encontró ningún bucket que comience con 'hardtechhub-bucket'.")
    except Exception as exc:
        print(f"❌ Error resolviendo el bucket S3: {exc}")
        raise SystemExit(1)

print(f"-> Conectando con bucket S3: {BUCKET_NAME}")

# ----------------------------------------------------
# 2. Generación de Eventos Simulados (PRODUCT_VIEW)
# ----------------------------------------------------
events = []
for _ in range(500):
    # Sesgo de visitas para consolidar un Top 5 definido en métricas
    prod_id = random.choices(
        [1, 2, 3, 4, 5, 10, 25, 40],
        weights=[35, 25, 15, 10, 5, 4, 3, 3]
    )[0]
    events.append({
        "event_type": "PRODUCT_VIEW",
        "product_id": prod_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

payload = json.dumps(events, indent=2).encode("utf-8")
s3_key = "raw/events/product_views.json"

# ----------------------------------------------------
# 3. Carga a S3
# ----------------------------------------------------
try:
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=payload,
        ContentType="application/json"
    )
    print(f"✅ 500 eventos subidos exitosamente a s3://{BUCKET_NAME}/{s3_key}")
except Exception as exc:
    print(f"❌ Error al subir eventos a S3: {exc}")
    raise SystemExit(1)
