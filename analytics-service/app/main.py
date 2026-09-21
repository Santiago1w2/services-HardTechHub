import json
import os
from collections import Counter
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import Depends, FastAPI, HTTPException
from .auth import require_admin


def get_s3_client() -> Any:
    # AWS credentials come from the standard SDK chain (e.g. an EC2 IAM role).
    return boto3.client("s3", endpoint_url=os.getenv("S3_ENDPOINT_URL") or None)



def get_bucket_name() -> str:
    return os.getenv("S3_BUCKET")


def get_events_prefix() -> str:
    return os.getenv("S3_EVENTS_PREFIX")


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


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"service": "analytics-service", "status": "healthy", "version": "1.0.0"}


@app.get("/api/analytics/events/count")
def count_events(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    events = load_events()
    event_types = Counter(event.get("event_type", "UNKNOWN") for event in events)
    return {
        "total_events": len(events),
        "by_type": dict(event_types),
        "prefix": get_events_prefix(),
    }


@app.get("/api/analytics/top-products")
def top_products(user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
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
