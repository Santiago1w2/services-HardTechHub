import json
import random
from datetime import datetime, timezone
import boto3

BUCKET_NAME = "hardtechhub-bucket-rodrigo"
s3 = boto3.client("s3", region_name="us-east-1")

# Simulamos visitas para los productos del 1 al 10 con distintas frecuencias
events = []
for _ in range(500):
    # Sesgamos los IDs para que haya un top 5 claro (ej. productos 1, 2 y 3 con mas vistas)
    prod_id = random.choices([1, 2, 3, 4, 5, 10, 25, 40], weights=[35, 25, 15, 10, 5, 4, 3, 3])[0]
    events.append({
        "event_type": "PRODUCT_VIEW",
        "product_id": prod_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    })

payload = json.dumps(events, indent=2).encode("utf-8")
s3_key = "raw/events/product_views.json"

s3.put_object(
    Bucket=BUCKET_NAME,
    Key=s3_key,
    Body=payload,
    ContentType="application/json"
)

print(f"-> 500 eventos subidos exitosamente a s3://{BUCKET_NAME}/{s3_key}")
