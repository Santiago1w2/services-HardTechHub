import os
from contextlib import contextmanager
from typing import Literal

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field


@contextmanager
def database():
    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("INVENTORY_POSTGRES_HOST", "postgres"),
            port=os.getenv("INVENTORY_POSTGRES_PORT", "5432"),
            dbname=os.getenv("INVENTORY_POSTGRES_DB", "hardtech_inventory"),
            user=os.environ["INVENTORY_POSTGRES_USER"],
            password=os.environ["INVENTORY_POSTGRES_PASSWORD"], connect_timeout=5,
            options="-c statement_timeout=10000 -c lock_timeout=5000",
        )
        with conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "Inventory already exists") from None
    except psycopg2.errors.CheckViolation:
        raise HTTPException(409, "Stock constraint violated") from None
    except psycopg2.Error:
        raise HTTPException(503, "Inventory database unavailable") from None
    finally:
        if conn is not None:
            conn.close()


app = FastAPI(title="Inventory Service", version="1.0.0")
app.add_middleware(CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://localhost:4173").split(",") if x.strip()],
    allow_credentials=False, allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Content-Type"])


class StockRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stock: int = Field(ge=0, le=2147483647, strict=True)
    reorder_point: int = Field(default=0, ge=0, le=2147483647, strict=True)


class CreateStockRequest(StockRequest):
    product_id: int = Field(gt=0, le=9007199254740991, strict=True)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(gt=0, le=9007199254740991, strict=True)
    quantity: int = Field(gt=0, le=2147483647, strict=True)


class ReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: int = Field(gt=0, le=9223372036854775807, strict=True)
    items: list[Item] = Field(min_length=1, max_length=100)


def movement(cursor, product_id, quantity, kind, order_id=None):
    cursor.execute("""INSERT INTO inventory_movements
        (product_id, order_id, movement_type, quantity) VALUES (%s,%s,%s,%s)""",
        (product_id, order_id, kind, quantity))


@app.get("/health")
def health():
    with database() as cursor:
        cursor.execute("SELECT 1 FROM inventory LIMIT 1")
    return {"service": "inventory-service", "status": "healthy"}


@app.get("/inventory")
def list_inventory(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    with database() as cursor:
        cursor.execute("SELECT *, stock - reserved_stock AS available_stock FROM inventory ORDER BY product_id LIMIT %s OFFSET %s", (limit, offset))
        return cursor.fetchall()


@app.get("/inventory/movements")
def list_movements(after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    with database() as cursor:
        cursor.execute("SELECT * FROM inventory_movements WHERE id > %s ORDER BY id LIMIT %s", (after_id, limit))
        return cursor.fetchall()


@app.post("/inventory", status_code=201)
def create_inventory(payload: CreateStockRequest):
    with database() as cursor:
        cursor.execute("""INSERT INTO inventory(product_id, stock, reorder_point) VALUES (%s,%s,%s)
            RETURNING *, stock - reserved_stock AS available_stock""", (payload.product_id, payload.stock, payload.reorder_point))
        result = cursor.fetchone()
        if payload.stock:
            movement(cursor, payload.product_id, payload.stock, "STOCK_IN")
        return result


def change_reservation(payload: ReservationRequest, action: Literal["RESERVE", "RELEASE", "SALE"]):
    quantities = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    items = [{"product_id": p, "quantity": q} for p, q in sorted(quantities.items())]
    target = {"RESERVE": "RESERVED", "RELEASE": "RELEASED", "SALE": "CONFIRMED"}[action]
    with database() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", (payload.order_id,))
        cursor.execute("SELECT * FROM inventory_reservations WHERE order_id=%s FOR UPDATE", (payload.order_id,))
        previous = cursor.fetchone()
        if previous:
            if previous["items"] != items:
                raise HTTPException(409, "Reservation items differ")
            if previous["status"] == target:
                return {"order_id": payload.order_id, "status": target}
            if previous["status"] != "RESERVED" or action == "RESERVE":
                raise HTTPException(409, "Invalid reservation transition")
        elif action == "SALE":
            raise HTTPException(409, "Reservation not found")
        elif action == "RELEASE":
            # A tombstone prevents delayed reservation after cancellation.
            cursor.execute("INSERT INTO inventory_reservations(order_id, status, items) VALUES (%s,%s,%s)",
                           (payload.order_id, target, Json(items)))
            return {"order_id": payload.order_id, "status": target}
        for item in items:
            pid, quantity = item["product_id"], item["quantity"]
            cursor.execute("SELECT * FROM inventory WHERE product_id=%s FOR UPDATE", (pid,))
            row = cursor.fetchone()
            if not row or (action == "RESERVE" and row["stock"] - row["reserved_stock"] < quantity):
                raise HTTPException(409, f"Insufficient stock for product {pid}")
            stock_delta = -quantity if action == "SALE" else 0
            reserve_delta = quantity if action == "RESERVE" else -quantity
            cursor.execute("""UPDATE inventory SET stock=stock+%s, reserved_stock=reserved_stock+%s,
                updated_at=CURRENT_TIMESTAMP WHERE product_id=%s""", (stock_delta, reserve_delta, pid))
            movement(cursor, pid, quantity, action, payload.order_id)
        cursor.execute("""INSERT INTO inventory_reservations(order_id,status,items) VALUES (%s,%s,%s)
            ON CONFLICT(order_id) DO UPDATE SET status=EXCLUDED.status, updated_at=CURRENT_TIMESTAMP""",
            (payload.order_id, target, Json(items)))
    return {"order_id": payload.order_id, "status": target}


@app.post("/inventory/reserve")
def reserve(payload: ReservationRequest):
    return change_reservation(payload, "RESERVE")


@app.post("/inventory/release")
def release(payload: ReservationRequest):
    return change_reservation(payload, "RELEASE")


@app.post("/inventory/confirm")
def confirm(payload: ReservationRequest):
    return change_reservation(payload, "SALE")


@app.get("/inventory/{product_id}")
def get_inventory(product_id: int):
    with database() as cursor:
        cursor.execute("SELECT *, stock - reserved_stock AS available_stock FROM inventory WHERE product_id=%s", (product_id,))
        result = cursor.fetchone()
        if not result:
            raise HTTPException(404, "Inventory not found")
        return result


@app.put("/inventory/{product_id}")
def update_inventory(product_id: int, payload: StockRequest):
    with database() as cursor:
        cursor.execute("SELECT * FROM inventory WHERE product_id=%s FOR UPDATE", (product_id,))
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "Inventory not found")
        if payload.stock < row["reserved_stock"]:
            raise HTTPException(409, "Cannot reduce stock below reservations")
        cursor.execute("""UPDATE inventory SET stock=%s, reorder_point=%s, updated_at=CURRENT_TIMESTAMP
            WHERE product_id=%s RETURNING *, stock - reserved_stock AS available_stock""",
            (payload.stock, payload.reorder_point, product_id))
        result = cursor.fetchone()
        delta = payload.stock - row["stock"]
        if delta:
            movement(cursor, product_id, delta, "STOCK_IN" if delta > 0 else "ADJUSTMENT")
        return result
