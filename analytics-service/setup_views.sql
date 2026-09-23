-- Base de datos analítica en Amazon Athena
CREATE DATABASE IF NOT EXISTS hardtech_analytics;

-- 1. Vista unificada de Catálogo (Productos + Categorías + Marcas)
CREATE OR REPLACE VIEW vw_product_catalog AS
SELECT 
    p.id AS product_id,
    p.name AS product_name,
    p.sku,
    p.price,
    p.stock,
    c.name AS category_name,
    b.name AS brand_name,
    p.created_at
FROM hardtech_analytics.products p
LEFT JOIN hardtech_analytics.categories c ON p.category_id = c.id
LEFT JOIN hardtech_analytics.brands b ON p.brand_id = b.id;

-- 2. Vista resumen por Categoría y Marca
CREATE OR REPLACE VIEW vw_category_brand_summary AS
SELECT 
    c.name AS category_name,
    b.name AS brand_name,
    COUNT(p.id) AS total_products,
    ROUND(AVG(p.price), 2) AS avg_price,
    SUM(p.stock) AS total_stock
FROM hardtech_analytics.products p
LEFT JOIN hardtech_analytics.categories c ON p.category_id = c.id
LEFT JOIN hardtech_analytics.brands b ON p.brand_id = b.id
GROUP BY c.name, b.name;
