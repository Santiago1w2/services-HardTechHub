import os

from fastapi import FastAPI, HTTPException
from psycopg2.extras import RealDictCursor
import psycopg2


def get_connection() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "hardtech_catalog"),
        user=os.getenv("POSTGRES_USER", "hardtech"),
        password=os.getenv("POSTGRES_PASSWORD", "hardtech"),
    )


app = FastAPI(title="Catalog Service", version="1.0.0")


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"service": "catalog-service", "status": "healthy", "version": "1.0.0"}


@app.get("/api/products")
def list_products() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT p.id, p.sku, p.name, p.description, p.price, p.specs, p.image_url,
                       c.name AS category, b.name AS brand
                FROM products p
                JOIN categories c ON c.id = p.category_id
                JOIN brands b ON b.id = p.brand_id
                WHERE p.is_active = TRUE
                ORDER BY p.id ASC
                """
            )
            rows = cursor.fetchall()
    return [dict(row) for row in rows]


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict:
    with get_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """
                SELECT p.id, p.sku, p.name, p.description, p.price, p.specs, p.image_url,
                       p.is_active, p.created_at, c.name AS category, b.name AS brand
                FROM products p
                JOIN categories c ON c.id = p.category_id
                JOIN brands b ON b.id = p.brand_id
                WHERE p.id = %s
                """,
                (product_id,),
            )
            row = cursor.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Product not found")

    return dict(row)
