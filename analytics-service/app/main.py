from fastapi.middleware.cors import CORSMiddleware
import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException
from .athena import query, configuration
from .pipeline import refresh


def get_s3_client() -> Any:
    return boto3.client("s3")



def get_bucket_name() -> str:
    return os.getenv("S3_BUCKET")


def get_events_prefix() -> str:
    return os.getenv("S3_EVENTS_PREFIX", "raw/events/")


def list_event_objects() -> list[str]:
    s3_client = get_s3_client()
    try:
        pages = s3_client.get_paginator("list_objects_v2").paginate(
            Bucket=get_bucket_name(), Prefix=get_events_prefix(),
        )
        return [obj["Key"] for page in pages for obj in page.get("Contents", [])
                if obj["Key"].endswith(".json")]
    except (ClientError, BotoCoreError):
        raise HTTPException(status_code=503, detail="S3 unavailable") from None



def load_events() -> list[dict[str, Any]]:
    s3_client = get_s3_client()
    events: list[dict[str, Any]] = []
    for key in list_event_objects():
        try:
            response = s3_client.get_object(Bucket=get_bucket_name(), Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise HTTPException(status_code=500, detail="S3 error") from exc

        try:
            payload = response["Body"].read().decode("utf-8").strip()
            if not payload:
                continue
            parsed = json.loads(payload)
            batch = parsed if isinstance(parsed, list) else [parsed]
            if any(not isinstance(event, dict) or
                   not isinstance(event.get("event_type", "UNKNOWN"), str) for event in batch):
                raise ValueError("Invalid event")
            events.extend(batch)
        except (ValueError, UnicodeError):
            raise HTTPException(status_code=502, detail="Invalid event data in S3") from None
        except (BotoCoreError, OSError):
            raise HTTPException(status_code=503, detail="S3 unavailable") from None
        finally:
            response["Body"].close()

    return events


app = FastAPI(title="Analytics Service", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"service": "analytics-service", "status": "healthy", "version": "1.0.0"}



@app.get("/health/aws")
def aws_health():
    settings = configuration()
    try:
        get_s3_client().head_bucket(Bucket=settings["S3_BUCKET"])
        boto3.client("glue").get_table(DatabaseName=settings["GLUE_DATABASE"], Name="business_snapshot")
        boto3.client("athena").get_work_group(WorkGroup=settings["ATHENA_WORKGROUP"])
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "AWS analytics unavailable") from None
    return {"status": "healthy"}


@app.post("/api/analytics/refresh")
def refresh_analytics():
    configuration()
    return refresh(get_s3_client(), get_bucket_name(), load_events())


@app.get("/api/analytics/events/count")
def count_events() -> dict[str, Any]:
    rows = query("SELECT event_type, COUNT(*) AS count FROM business_snapshot GROUP BY event_type")
    return {"total_events": sum(row["count"] for row in rows),
            "by_type": {row["event_type"]: row["count"] for row in rows}}


@app.get("/api/analytics/summary")
def summary():
    rows = query("""
        SELECT COUNT_IF(event_type='ORDER') AS orders,
            COUNT_IF(event_type='ORDER' AND order_status IN ('PAID','SHIPPED')) AS sales,
            COALESCE(SUM(CASE WHEN event_type='ORDER' AND order_status IN ('PAID','SHIPPED') THEN total_amount ELSE 0 END),0) AS revenue,
            COALESCE(SUM(CASE WHEN event_type='ORDER_ITEM' AND order_status IN ('PAID','SHIPPED') THEN quantity ELSE 0 END),0) AS units_sold,
            COUNT(DISTINCT CASE WHEN event_type='ORDER_ITEM' AND order_status IN ('PAID','SHIPPED') THEN product_id END) AS products_sold
        FROM business_snapshot
    """)
    return rows[0] if rows else {"orders": 0, "sales": 0, "revenue": "0"}


@app.get("/api/analytics/top-products")
def top_products() -> dict[str, Any]:
    return {"top_products": query("""
        SELECT product_id, MAX(product_name) AS product_name, SUM(quantity) AS units,
            SUM(subtotal) AS revenue FROM business_snapshot
        WHERE event_type='ORDER_ITEM' AND order_status IN ('PAID','SHIPPED')
        GROUP BY product_id ORDER BY units DESC, product_id LIMIT 5
    """)}


@app.get("/api/analytics/top-categories")
def top_categories():
    return {"categories": query("""
        SELECT category, SUM(quantity) AS units, SUM(subtotal) AS revenue FROM business_snapshot
        WHERE event_type='ORDER_ITEM' AND order_status IN ('PAID','SHIPPED')
        GROUP BY category ORDER BY units DESC, category
    """)}


@app.get("/api/analytics/trends")
def trends():
    return {"trends": query("""
        SELECT substr(created_at,1,10) AS day, COUNT(*) AS sales, SUM(total_amount) AS revenue
        FROM business_snapshot WHERE event_type='ORDER' AND order_status IN ('PAID','SHIPPED')
        GROUP BY substr(created_at,1,10) ORDER BY day
    """)}


@app.get("/api/analytics/inventory-movements")
def inventory_movements():
    return {"movements": query("""
        SELECT movement_type, COUNT(*) AS movements, SUM(quantity) AS units
        FROM business_snapshot WHERE event_type='INVENTORY_MOVEMENT'
        GROUP BY movement_type ORDER BY movement_type
    """)}


@app.get("/api/analytics/top-views")
def top_views():
    return {"top_products": query("""
        SELECT product_id, COUNT(*) AS views FROM business_snapshot WHERE event_type='PRODUCT_VIEW'
        GROUP BY product_id ORDER BY views DESC, product_id LIMIT 5
    """)}
@app.get("/api/analytics/product-catalog")
def product_catalog() -> dict[str, Any]:
    rows = query("""
        SELECT *
        FROM vw_product_catalog
        ORDER BY product_id
    """)

    return {
        "products": rows,
        "total": len(rows)
    }


@app.get("/api/analytics/category-brand-summary")
def category_brand_summary() -> dict[str, Any]:
    rows = query("""
        SELECT *
        FROM vw_category_brand_summary
        ORDER BY category, brand
    """)

    return {
        "summary": rows,
        "total": len(rows)
    }