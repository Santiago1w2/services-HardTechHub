import os
from decimal import Decimal
from typing import Any, Literal
from datetime import date, datetime, time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json

from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import JSONResponse
import hashlib
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict
import pymysql


def get_connection() -> pymysql.connections.Connection:
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE"),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False, connect_timeout=5, read_timeout=20, write_timeout=20,
    )


app = FastAPI(
    title="Order Service API - HardTechHub",
    description="Microservicio de órdenes y pedidos conectado a MySQL",
    version="1.0.0",
    docs_url="/docs"
)

# 1. Habilitar CORS para peticiones desde AWS Amplify y desarrollo local
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)


def get_catalog_service_url() -> str:
    # Puerto 8002 que usa catalog-service
    return os.getenv("CATALOG_SERVICE_URL")


def fetch_product_snapshot(product_id: int) -> dict[str, Any]:
    product_url = f"{get_catalog_service_url()}/api/products/{product_id}"
    try:
        with urlopen(product_url, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(status_code=400, detail=f"Product {product_id} not found in catalog") from exc
        raise HTTPException(status_code=502, detail="Catalog Service error") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=503, detail="Catalog Service unavailable") from exc


class OrderItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, le=9007199254740991, strict=True)
    quantity: int = Field(gt=0, le=2147483647, strict=True)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[OrderItemRequest] = Field(min_length=1, max_length=100)


class UpdateStatusRequest(BaseModel):
    status: str


VALID_STATUSES = {"RESERVING", "PENDING", "PAID", "SHIPPED", "CANCELLED"}


@app.get("/health", tags=["Health"])
def healthcheck() -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM orders LIMIT 1")
    finally:
        conn.close()
    return {"service": "order-service", "status": "healthy", "version": "1.0.0"}


@app.get("/api/orders", tags=["Orders"])
def get_all_orders() -> list[dict[str, Any]]:
    """Listado general de órdenes para el Frontend"""
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 50")
            orders = cursor.fetchall()
            for o in orders:
                if "subtotal" in o: o["subtotal"] = str(o["subtotal"])
                if "tax" in o: o["tax"] = str(o["tax"])
                if "shipping_cost" in o: o["shipping_cost"] = str(o["shipping_cost"])
                if "total_amount" in o: o["total_amount"] = str(o["total_amount"])
                if "created_at" in o and o["created_at"]: o["created_at"] = str(o["created_at"])
                if "updated_at" in o and o["updated_at"]: o["updated_at"] = str(o["updated_at"])
        return orders
    finally:
        conn.close()



@app.get("/api/admin/orders", tags=["Orders"])
def get_admin_orders(
    page: int = Query(default=1, ge=1, le=1000000),
    limit: int = Query(default=20, ge=1, le=100),
    status: Literal["RESERVING", "PENDING", "PAID", "SHIPPED", "CANCELLED"] | None = None,
    order_id: int | None = Query(default=None, ge=1),
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="Invalid date range")

    filters: list[str] = []
    values: list[Any] = []
    if status is not None:
        filters.append("status = %s")
        values.append(status)
    if order_id is not None:
        filters.append("id = %s")
        values.append(order_id)
    if date_from is not None:
        filters.append("created_at >= %s")
        values.append(datetime.combine(date_from, time.min))
    if date_to is not None:
        filters.append("created_at <= %s")
        values.append(datetime.combine(date_to, time.max))
    where = " WHERE " + " AND ".join(filters) if filters else ""

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM orders" + where, values)
            total = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT * FROM orders" + where
                + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
                [*values, limit, (page - 1) * limit],
            )
            items = cursor.fetchall()
            for item in items:
                for field in ("subtotal", "tax", "shipping_cost", "total_amount"):
                    if field in item:
                        item[field] = str(item[field])
                for field in ("created_at", "updated_at"):
                    if item.get(field) is not None:
                        item[field] = str(item[field])
        return {"items": list(items), "page": page, "limit": limit, "total": total}
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=503, detail="MySQL unavailable") from exc
    finally:
        conn.close()

@app.post("/api/orders", status_code=201, tags=["Orders"])
def create_order(payload: CreateOrderRequest, idempotency_key: str | None = Header(default=None, min_length=1, max_length=100)) -> dict[str, Any]:
    if not payload.items:
        raise HTTPException(status_code=400, detail="Order items are required")

    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    if any(quantity > 2147483647 for quantity in quantities.values()):
        raise HTTPException(422, "Combined quantity exceeds inventory capacity")

    request_hash = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    if idempotency_key:
        existing = find_request(idempotency_key, request_hash)
        if existing:
            return resume_order(existing)

    order_snapshots = []
    subtotal = Decimal("0.00")
    for item in payload.items:
        product = fetch_product_snapshot(item.product_id)
        if product.get("is_active") is not True:
            raise HTTPException(status_code=400, detail="Product is inactive")
        unit_price = Decimal(str(product["price"]))
        item_subtotal = unit_price * item.quantity
        order_snapshots.append(
            {
                "product_id": item.product_id,
                "product_sku": product["sku"],
                "product_name": product["name"],
                "product_category": product["category"],
                "quantity": item.quantity,
                "unit_price": unit_price,
                "subtotal": item_subtotal,
            }
        )
        subtotal += item_subtotal

    tax = (subtotal * Decimal("0.18")).quantize(Decimal("0.01"))
    shipping_cost = Decimal("25.00")
    total_amount = subtotal + tax + shipping_cost
    if total_amount > Decimal("99999999999999.99"):
        raise HTTPException(422, "Order total exceeds supported amount")

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO orders (status, subtotal, tax, shipping_cost, total_amount, request_key, request_hash)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                ("RESERVING", str(subtotal), str(tax), str(shipping_cost), str(total_amount), idempotency_key, request_hash),
            )
            order_id = cursor.lastrowid

            for item in order_snapshots:
                cursor.execute(
                    """
                    INSERT INTO order_items
                    (order_id, product_id, product_sku, product_name, product_category, quantity, unit_price, subtotal)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        order_id,
                        item["product_id"],
                        item["product_sku"],
                        item["product_name"],
                        item["product_category"],
                        item["quantity"],
                        str(item["unit_price"]),
                        str(item["subtotal"]),
                    ),
                )

        conn.commit()
    except pymysql.IntegrityError:
        conn.rollback()
        if idempotency_key:
            existing = find_request(idempotency_key, request_hash)
            if existing:
                return resume_order(existing)
        raise HTTPException(409, "Order constraint violated") from None
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail="MySQL error") from exc
    finally:
        conn.close()

    result = resume_order(order_id)
    return {**result, "total_amount": str(total_amount)}


@app.get("/api/orders/{order_id}", tags=["Orders"])
def get_order(order_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM orders WHERE id = %s", (order_id,))
            order = cursor.fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")


            if "subtotal" in order: order["subtotal"] = str(order["subtotal"])
            if "tax" in order: order["tax"] = str(order["tax"])
            if "shipping_cost" in order: order["shipping_cost"] = str(order["shipping_cost"])
            if "total_amount" in order: order["total_amount"] = str(order["total_amount"])
            if "created_at" in order and order["created_at"]: order["created_at"] = str(order["created_at"])
            if "updated_at" in order and order["updated_at"]: order["updated_at"] = str(order["updated_at"])

            cursor.execute("SELECT * FROM order_items WHERE order_id = %s ORDER BY id ASC", (order_id,))
            items = cursor.fetchall()
            for item in items:
                if "unit_price" in item: item["unit_price"] = str(item["unit_price"])
                if "subtotal" in item: item["subtotal"] = str(item["subtotal"])
    finally:
        conn.close()

    return {"order": order, "items": items}


@app.patch("/api/orders/{order_id}/status", tags=["Orders"])
def update_order_status(order_id: int, payload: UpdateStatusRequest) -> dict[str, Any]:
    if payload.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, status FROM orders WHERE id = %s FOR UPDATE", (order_id,))
            order = cursor.fetchone()
            if not order:
                raise HTTPException(status_code=404, detail="Order not found")
            if order["status"] == payload.status:
                return {"order_id": order_id, "status": payload.status}
            allowed = {"RESERVING": {"CANCELLED"}, "PENDING": {"PAID", "CANCELLED"}, "PAID": {"SHIPPED"}}
            if payload.status not in allowed.get(order["status"], set()):
                raise HTTPException(status_code=409, detail="Invalid order transition")
            if payload.status in {"PAID", "CANCELLED"}:
                inventory_operation("confirm" if payload.status == "PAID" else "release",
                                    order_id, order_items(cursor, order_id))
            cursor.execute(
                "UPDATE orders SET status = %s WHERE id = %s",
                (payload.status, order_id),
            )
        conn.commit()
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=500, detail="MySQL error") from exc
    finally:
        conn.close()
    return {"order_id": order_id, "status": payload.status}

@app.exception_handler(pymysql.MySQLError)
async def database_error(_request, _exception):
    return JSONResponse(status_code=503, content={"detail": "Orders database unavailable"})


def inventory_operation(action: str, order_id: int, items: list[dict]) -> dict:
    url = os.getenv("INVENTORY_SERVICE_URL", "http://inventory-service:8006").rstrip("/")
    request = Request(f"{url}/inventory/{action}",
        data=json.dumps({"order_id": order_id, "items": items}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read())
    except HTTPError as exc:
        if exc.code == 409:
            raise HTTPException(409, "Inventory operation rejected") from None
        raise HTTPException(503, "Inventory unavailable; retry the same order") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise HTTPException(503, "Inventory unavailable; retry the same order") from None


def order_items(cursor, order_id):
    cursor.execute("SELECT product_id, SUM(quantity) AS quantity FROM order_items WHERE order_id=%s GROUP BY product_id ORDER BY product_id", (order_id,))
    return [{"product_id": int(x["product_id"]), "quantity": int(x["quantity"])} for x in cursor.fetchall()]


def find_request(key, request_hash):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, request_hash FROM orders WHERE request_key=%s", (key,))
            order = cursor.fetchone()
            if order and order["request_hash"] != request_hash:
                raise HTTPException(409, "Idempotency key has different items")
            return order["id"] if order else None
    finally:
        conn.close()


@app.post("/api/orders/{order_id}/retry", tags=["Orders"])
def resume_order(order_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, status, total_amount FROM orders WHERE id=%s FOR UPDATE", (order_id,))
            order = cursor.fetchone()
            if not order:
                raise HTTPException(404, "Order not found")
            if order["status"] == "CANCELLED":
                raise HTTPException(409, {"message": "Order cancelled", "order_id": order_id})
            if order["status"] != "RESERVING":
                return {"order_id": order_id, "status": order["status"], "total_amount": str(order["total_amount"])}
            items = order_items(cursor, order_id)
            try:
                inventory_operation("reserve", order_id, items)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise HTTPException(503, {"message": "Reservation pending; retry this order", "order_id": order_id}) from None
                inventory_operation("release", order_id, items)
                cursor.execute("UPDATE orders SET status='CANCELLED' WHERE id=%s", (order_id,))
                conn.commit()
                raise HTTPException(409, {"message": "Insufficient stock", "order_id": order_id}) from None
            cursor.execute("UPDATE orders SET status='PENDING' WHERE id=%s", (order_id,))
        conn.commit()
        return {"order_id": order_id, "status": "PENDING", "total_amount": str(order["total_amount"])}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@app.get("/api/exports/orders", tags=["Analytics"])
def export_orders(after_id: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=1000)):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, status, subtotal, tax, shipping_cost, total_amount, created_at, updated_at FROM orders WHERE id>%s ORDER BY id LIMIT %s", (after_id, limit))
            result = list(cursor.fetchall())
            for order in result:
                cursor.execute("SELECT * FROM order_items WHERE order_id=%s ORDER BY id", (order["id"],))
                order["items"] = list(cursor.fetchall())
                for field in ("subtotal", "tax", "shipping_cost", "total_amount"):
                    order[field] = str(order[field])
                for item in order["items"]:
                    item["unit_price"] = str(item["unit_price"])
                    item["subtotal"] = str(item["subtotal"])
            return result
    finally:
        conn.close()
