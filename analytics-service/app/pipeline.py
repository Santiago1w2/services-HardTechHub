import json
import os
import threading
from datetime import datetime, timezone
from urllib.request import urlopen
from urllib.error import URLError
from fastapi import HTTPException
from botocore.exceptions import BotoCoreError, ClientError

_refresh_lock = threading.Lock()


def fetch_pages(base, path):
    after_id = 0
    while True:
        try:
            with urlopen(f"{base.rstrip('/')}{path}?after_id={after_id}&limit=500", timeout=15) as response:
                batch = json.loads(response.read())
            if not isinstance(batch, list):
                raise ValueError()
            if not batch:
                return
            next_id = int(batch[-1]["id"])
            if next_id <= after_id:
                raise ValueError()
        except (URLError, OSError, ValueError, KeyError, TypeError):
            raise HTTPException(503, "Operational export unavailable") from None
        yield from batch
        after_id = next_id


def refresh(s3, bucket, events):
    if not bucket:
        raise HTTPException(503, "S3_BUCKET is required")
    if not _refresh_lock.acquire(blocking=False):
        raise HTTPException(409, "Analytics refresh already running")
    try:
        records = []
        for order in fetch_pages(os.getenv("ORDER_SERVICE_URL", "http://order-service:8003"), "/api/exports/orders"):
            common = {"order_id": order["id"], "order_status": order["status"],
                      "created_at": order["created_at"], "updated_at": order["updated_at"]}
            records.append({**common, "event_type": "ORDER", "total_amount": order["total_amount"]})
            for item in order["items"]:
                records.append({**common, "event_type": "ORDER_ITEM", "product_id": item["product_id"],
                    "category": item["product_category"], "product_name": item["product_name"],
                    "quantity": item["quantity"], "subtotal": item["subtotal"]})
        for move in fetch_pages(os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8006"), "/inventory/movements"):
            records.append({**move, "event_type": "INVENTORY_MOVEMENT", "movement_id": move["id"]})
        # Preserve existing product-view ingestion; obsolete personal fields are never exported.
        for event in events:
            if event.get("event_type") == "PRODUCT_VIEW" and event.get("product_id") is not None:
                records.append({"event_type": "PRODUCT_VIEW", "product_id": int(event["product_id"]),
                                "created_at": event.get("created_at")})
        payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records).encode()
        key = os.getenv("S3_DATA_PREFIX", "analytics/").strip("/") + "/business/current.jsonl"
        s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/x-ndjson",
                      ServerSideEncryption="AES256")
        return {"records": len(records), "key": key, "refreshed_at": datetime.now(timezone.utc).isoformat()}
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "S3 unavailable") from None
    finally:
        _refresh_lock.release()
