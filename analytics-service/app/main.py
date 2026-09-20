import json
import os
from collections import Counter
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException


def get_s3_client() -> Any:
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("S3_ENDPOINT_URL", "http://localstack:4566"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


def get_bucket_name() -> str:
    return os.getenv("S3_BUCKET", "hardtech-datalake")


def get_events_prefix() -> str:
    return os.getenv("S3_EVENTS_PREFIX", "raw/events/")


def list_event_objects() -> list[str]:
    s3_client = get_s3_client()
    try:
        response = s3_client.list_objects_v2(Bucket=get_bucket_name(), Prefix=get_events_prefix())
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(status_code=500, detail="S3 error") from exc

    return [obj["Key"] for obj in response.get("Contents", []) if obj["Key"].endswith(".json")]


def load_events() -> list[dict[str, Any]]:
    s3_client = get_s3_client()
    events: list[dict[str, Any]] = []
    for key in list_event_objects():
        try:
            response = s3_client.get_object(Bucket=get_bucket_name(), Key=key)
        except (ClientError, BotoCoreError) as exc:
            raise HTTPException(status_code=500, detail="S3 error") from exc

        payload = response["Body"].read().decode("utf-8").strip()
        if not payload:
            continue

        parsed = json.loads(payload)
        if isinstance(parsed, list):
            events.extend(parsed)
        else:
            events.append(parsed)

    return events


app = FastAPI(title="Analytics Service", version="1.0.0")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"service": "analytics-service", "status": "healthy", "version": "1.0.0"}


@app.get("/api/analytics/events/count")
def count_events() -> dict[str, Any]:
    events = load_events()
    event_types = Counter(event.get("event_type", "UNKNOWN") for event in events)
    return {
        "total_events": len(events),
        "by_type": dict(event_types),
        "prefix": get_events_prefix(),
    }


@app.get("/api/analytics/top-products")
def top_products() -> dict[str, Any]:
    events = load_events()
    product_views = Counter(
        str(event["product_id"])
        for event in events
        if event.get("event_type") == "PRODUCT_VIEW" and event.get("product_id") is not None
    )
    top = [
        {"product_id": product_id, "views": views}
        for product_id, views in product_views.most_common(5)
    ]
    return {"top_products": top}
