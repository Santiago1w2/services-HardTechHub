import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from pydantic import BaseModel, ConfigDict, Field


def get_client():
    return MongoClient(
        os.environ["INVENTORY_MONGO_URI"],
        appname="inventory-service",
        maxPoolSize=10,
        connectTimeoutMS=5000,
        serverSelectionTimeoutMS=10000,
    )


def get_db(client):
    return client[os.getenv("INVENTORY_MONGO_DB", "hardtech_inventory")]


app = FastAPI(title="Inventory Service - HardTechHub", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",") if x.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


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


def read_inventory_doc(doc):
    stock = doc.get("stock", 0)
    reserved = doc.get("reserved_stock", 0)
    return {
        "product_id": doc["_id"],
        "stock": stock,
        "reserved_stock": reserved,
        "reorder_point": doc.get("reorder_point", 0),
        "available_stock": stock - reserved,
        "updated_at": doc.get("updated_at"),
    }


def now_utc():
    return datetime.now(timezone.utc)


def next_movement_id(db):
    doc = db.counters.find_one_and_update(
        {"_id": "movements"},
        {"$inc": {"value": 1}},
        return_document=True,
    )
    if doc is None or "value" not in doc:
        raise HTTPException(503, "Inventory database unavailable")
    return doc["value"]


def record_movements(db, movements):
    docs = [{"_id": next_movement_id(db), **movement} for movement in movements]
    if docs:
        db.inventory_movements.insert_many(docs, ordered=True)


@app.get("/health")
def health():
    try:
        with get_client() as client:
            get_db(client).inventory.find_one()
    except PyMongoError:
        raise HTTPException(503, "Inventory database unavailable") from None
    return {"service": "inventory-service", "status": "healthy"}


@app.get("/inventory")
def list_inventory(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    try:
        with get_client() as client:
            db = get_db(client)
            rows = db.inventory.find().sort("_id", 1).skip(offset).limit(limit)
            return [read_inventory_doc(doc) for doc in rows]
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc


@app.get("/inventory/movements")
def list_movements(after_id: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
    try:
        with get_client() as client:
            db = get_db(client)
            rows = (db.inventory_movements.find({"_id": {"$gt": after_id}})
                    .sort("_id", 1).limit(limit))
            return [{
                "id": doc["_id"],
                "product_id": doc["product_id"],
                "order_id": doc.get("order_id"),
                "movement_type": doc["movement_type"],
                "quantity": doc["quantity"],
                "created_at": doc.get("created_at"),
            } for doc in rows]
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc


@app.post("/inventory", status_code=201)
def create_inventory(payload: CreateStockRequest):
    try:
        with get_client() as client:
            db = get_db(client)
            doc = {
                "_id": payload.product_id,
                "stock": payload.stock,
                "reserved_stock": 0,
                "reorder_point": payload.reorder_point,
                "updated_at": now_utc(),
            }
            try:
                db.inventory.insert_one(doc)
            except DuplicateKeyError:
                raise HTTPException(409, "Inventory already exists") from None
            if payload.stock:
                record_movements(db, [{
                    "product_id": payload.product_id,
                    "order_id": None,
                    "movement_type": "STOCK_IN",
                    "quantity": payload.stock,
                    "created_at": now_utc(),
                }])
            return read_inventory_doc(doc)
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc


def apply_stock_updates(db, items, action):
    applied = []
    for item in items:
        pid, qty = item["product_id"], item["quantity"]
        if action == "RESERVE":
            amount = {"reserved_stock": qty}
            available_expr = {"$subtract": ["$stock", "$reserved_stock"]}
            condition = {"_id": pid, "$expr": {"$gte": [available_expr, qty]}}
        elif action == "RELEASE":
            amount = {"reserved_stock": -qty}
            condition = {"_id": pid, "$expr": {"$gte": ["$reserved_stock", qty]}}
        else:
            amount = {"stock": -qty, "reserved_stock": -qty}
            condition = {"_id": pid, "$expr": {
                "$and": [
                    {"$gte": ["$reserved_stock", qty]},
                    {"$gte": ["$stock", qty]},
                ],
            }}
        result = db.inventory.update_one(
            condition,
            {"$inc": amount, "$set": {"updated_at": now_utc()}},
        )
        if result.matched_count == 0:
            for applied_pid, applied_qty in applied:
                if action == "RESERVE":
                    rollback = {"reserved_stock": -applied_qty}
                elif action == "RELEASE":
                    rollback = {"reserved_stock": applied_qty}
                else:
                    rollback = {"stock": applied_qty, "reserved_stock": applied_qty}
                db.inventory.update_one(
                    {"_id": applied_pid},
                    {"$inc": rollback, "$set": {"updated_at": now_utc()}},
                )
            raise HTTPException(409, f"Insufficient stock for product {pid}") from None
        applied.append((pid, qty))
    return applied


def change_reservation(payload: ReservationRequest, action: Literal["RESERVE", "RELEASE", "SALE"]):
    quantities = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.quantity
    items = [{"product_id": p, "quantity": q} for p, q in sorted(quantities.items())]
    target = {"RESERVE": "RESERVED", "RELEASE": "RELEASED", "SALE": "CONFIRMED"}[action]

    try:
        with get_client() as client:
            db = get_db(client)

            previous = db.inventory_reservations.find_one({"order_id": payload.order_id})
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
                db.inventory_reservations.replace_one(
                    {"order_id": payload.order_id},
                    {"order_id": payload.order_id, "status": target, "items": items,
                     "updated_at": now_utc()},
                    upsert=True,
                )
                return {"order_id": payload.order_id, "status": target}

            applied = apply_stock_updates(db, items, action)

            record_movements(db, [{
                "product_id": pid,
                "order_id": payload.order_id,
                "movement_type": action,
                "quantity": qty,
                "created_at": now_utc(),
            } for pid, qty in applied])

            db.inventory_reservations.replace_one(
                {"order_id": payload.order_id},
                {"order_id": payload.order_id, "status": target, "items": items,
                 "updated_at": now_utc()},
                upsert=True,
            )
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc
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
    try:
        with get_client() as client:
            db = get_db(client)
            doc = db.inventory.find_one({"_id": product_id})
            if not doc:
                raise HTTPException(404, "Inventory not found")
            return read_inventory_doc(doc)
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc


@app.put("/inventory/{product_id}")
def update_inventory(product_id: int, payload: StockRequest):
    try:
        with get_client() as client:
            db = get_db(client)
            existing = db.inventory.find_one({"_id": product_id})
            if not existing:
                raise HTTPException(404, "Inventory not found")
            if payload.stock < existing.get("reserved_stock", 0):
                raise HTTPException(409, "Cannot reduce stock below reservations")
            db.inventory.update_one(
                {"_id": product_id},
                {"$set": {"stock": payload.stock, "reorder_point": payload.reorder_point,
                          "updated_at": now_utc()}},
            )
            delta = payload.stock - existing.get("stock", 0)
            if delta:
                record_movements(db, [{
                    "product_id": product_id,
                    "order_id": None,
                    "movement_type": "STOCK_IN" if delta > 0 else "ADJUSTMENT",
                    "quantity": delta if delta > 0 else -delta,
                    "created_at": now_utc(),
                }])
            updated = db.inventory.find_one({"_id": product_id})
            return read_inventory_doc(updated)
    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(503, "Inventory database unavailable") from exc