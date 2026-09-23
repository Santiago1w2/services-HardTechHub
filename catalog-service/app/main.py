import os
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg2
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PRICE = Decimal("999999999999.99")


def get_connection():
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "hardtech_catalog"),
        user=os.getenv("POSTGRES_USER", "catalog_app"),
        password=os.environ["POSTGRES_PASSWORD"],
        cursor_factory=RealDictCursor,
        connect_timeout=5, options="-c statement_timeout=10000",
    )


app = FastAPI(
    title="Catalog Service API - HardTechHub",
    description="Microservicio de catalogo de productos conectado a PostgreSQL",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _exc):
    return JSONResponse(status_code=400, content={"detail": "Invalid request"})


@app.exception_handler(psycopg2.Error)
async def database_error(_request, _exc):
    return JSONResponse(status_code=503, content={"detail": "Catalog database unavailable"})


def to_price(value) -> str:
    return Decimal(value).quantize(Decimal("0.01")).__str__()


def row_to_product(row: dict | None):
    if row is None:
        return None
    specs = row.get("specs") or {}
    created_at = row.get("created_at")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    return {
        "id": row["id"],
        "sku": row["sku"],
        "name": row["name"],
        "description": row.get("description"),
        "price": to_price(row["price"]),
        "specs": specs,
        "specifications": specs,
        "image_url": row.get("image_url"),
        "category": row.get("category"),
        "brand": row.get("brand"),
        "is_active": row.get("is_active", True),
        "created_at": created_at,
    }


PRODUCT_SELECT = """
    SELECT p.id, p.sku, p.name, p.description, p.price, p.specs, p.image_url,
           p.is_active, p.created_at, c.name AS category, b.name AS brand
    FROM products p
    JOIN categories c ON c.id = p.category_id
    JOIN brands b ON b.id = p.brand_id
"""


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: int = Field(gt=0)
    brand_id: int = Field(gt=0)
    sku: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=180)
    description: str | None = None
    price: Decimal = Field(ge=0, le=MAX_PRICE)
    specs: dict[str, Any] | None = None
    specifications: dict[str, Any] | None = None
    image_url: str | None = None


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = None
    price: Decimal | None = Field(default=None, ge=0, le=MAX_PRICE)
    specs: dict[str, Any] | None = None
    specifications: dict[str, Any] | None = None
    image_url: str | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def at_least_one_field(self):
        fields = (self.name, self.description, self.price, self.specs,
                  self.specifications, self.image_url, self.is_active)
        if all(value is None for value in fields):
            raise ValueError("At least one field is required")
        return self


@app.get("/health", tags=["Health"])
def healthcheck():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
    finally:
        conn.close()
    return {"service": "catalog-service", "status": "healthy", "version": "1.0.0"}


@app.get("/api/products", tags=["Products"])
def list_products():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(PRODUCT_SELECT + "WHERE p.is_active = TRUE ORDER BY p.id ASC")
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [row_to_product(row) for row in rows]


@app.get("/api/products/{product_id}", tags=["Products"])
def get_product(product_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(PRODUCT_SELECT + "WHERE p.id = %s", (product_id,))
            row = cursor.fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Product not found")
    return row_to_product(row)


@app.post("/api/products", status_code=201, tags=["Products"])
def create_product(payload: ProductCreate):
    conn = get_connection()
    try:
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id FROM categories WHERE id = %s", (payload.category_id,))
                category = cursor.fetchone()
                cursor.execute("SELECT id, name FROM brands WHERE id = %s", (payload.brand_id,))
                brand = cursor.fetchone()
                if not category or not brand:
                    raise HTTPException(status_code=400, detail="Invalid category or brand")
                specs = payload.specifications if payload.specifications is not None else (payload.specs or {})
                cursor.execute(
                    """
                    INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sku) DO NOTHING
                    RETURNING id
                    """,
                    (payload.category_id, payload.brand_id, payload.sku, payload.name,
                     payload.description, payload.price, Json(jsonable_encoder(specs)), payload.image_url),
                )
                row = cursor.fetchone()
            if not row:
                conn.rollback()
                raise HTTPException(status_code=409, detail="Product already exists")
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Product already exists") from None
    finally:
        conn.close()
    return {"id": row["id"], "sku": payload.sku, "name": payload.name, "price": to_price(payload.price)}


@app.put("/api/products/{product_id}", tags=["Products"])
def update_product(product_id: int, payload: ProductUpdate):
    fields: dict[str, Any] = {}
    if payload.name is not None:
        fields["name"] = payload.name
    if payload.description is not None:
        fields["description"] = payload.description
    if payload.price is not None:
        fields["price"] = payload.price
    if payload.specifications is not None or payload.specs is not None:
        fields["specs"] = Json(jsonable_encoder(
            payload.specifications if payload.specifications is not None else payload.specs))
    if payload.image_url is not None:
        fields["image_url"] = payload.image_url
    if payload.is_active is not None:
        fields["is_active"] = payload.is_active

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE products SET " + ", ".join(f"{column} = %s" for column in fields) +
                " WHERE id = %s",
                [*fields.values(), product_id],
            )
            updated = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    if updated == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"updated": True}


@app.delete("/api/products/{product_id}", tags=["Products"])
def delete_product(product_id: int):
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE products SET is_active = FALSE WHERE id = %s", (product_id,))
            deleted = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"deleted": True}


@app.get("/api/categories", tags=["Products"])
def list_categories():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM categories ORDER BY name, id")
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@app.get("/api/brands", tags=["Products"])
def list_brands():
    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id, name FROM brands ORDER BY name, id")
            rows = cursor.fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


@app.get("/api/admin/products", tags=["Products"])
def admin_products(
    page: int = Query(default=1, ge=1, le=1000000),
    limit: int = Query(default=20, ge=1, le=100),
    status: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=180),
    category_id: int | None = Query(default=None, ge=1, le=2147483647),
    brand_id: int | None = Query(default=None, ge=1, le=2147483647),
):
    if status not in ("all", "active", "inactive"):
        raise HTTPException(status_code=400, detail="Invalid status")

    conditions: list[str] = []
    values: list[Any] = []
    if status != "all":
        conditions.append("p.is_active = %s")
        values.append(status == "active")
    if category_id is not None:
        conditions.append("p.category_id = %s")
        values.append(category_id)
    if brand_id is not None:
        conditions.append("p.brand_id = %s")
        values.append(brand_id)
    if q and q.strip():
        pattern = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(
            "(p.name ILIKE %s ESCAPE '\\' OR p.sku ILIKE %s ESCAPE '\\' "
            "OR c.name ILIKE %s ESCAPE '\\' OR b.name ILIKE %s ESCAPE '\\')"
        )
        values.extend([f"%{pattern}%"] * 4)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM products p "
                "JOIN categories c ON c.id = p.category_id "
                "JOIN brands b ON b.id = p.brand_id" + where,
                values,
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                PRODUCT_SELECT + where + " ORDER BY p.id DESC LIMIT %s OFFSET %s",
                [*values, limit, (page - 1) * limit],
            )
            items = [row_to_product(row) for row in cursor.fetchall()]
    finally:
        conn.close()
    return {"items": items, "page": page, "limit": limit, "total": total}