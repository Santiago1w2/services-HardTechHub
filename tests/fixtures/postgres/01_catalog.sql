CREATE TABLE IF NOT EXISTS brands (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    country VARCHAR(120)
);

CREATE TABLE IF NOT EXISTS categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(120) NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id),
    brand_id INTEGER NOT NULL REFERENCES brands(id),
    sku VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(180) NOT NULL,
    description TEXT,
    price NUMERIC(10, 2) NOT NULL,
    specs JSONB NOT NULL DEFAULT '{}'::jsonb,
    image_url TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_products_category_id ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_products_brand_id ON products(brand_id);
CREATE INDEX IF NOT EXISTS idx_products_price ON products(price);

-- Brands
INSERT INTO brands (name, country) SELECT 'AMD',     'USA' WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'AMD');
INSERT INTO brands (name, country) SELECT 'NVIDIA',  'USA' WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'NVIDIA');
INSERT INTO brands (name, country) SELECT 'Corsair', 'USA' WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'Corsair');
INSERT INTO brands (name, country) SELECT 'ASUS',    'Taiwan' WHERE NOT EXISTS (SELECT 1 FROM brands WHERE name = 'ASUS');

-- Categories
INSERT INTO categories (name, description) SELECT 'CPU',         'Procesadores para computadoras'   WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'CPU');
INSERT INTO categories (name, description) SELECT 'Motherboard', 'Tarjetas madre para computadoras' WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'Motherboard');
INSERT INTO categories (name, description) SELECT 'GPU',         'Tarjetas graficas'                WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'GPU');
INSERT INTO categories (name, description) SELECT 'RAM',         'Memorias RAM'                     WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'RAM');
INSERT INTO categories (name, description) SELECT 'PSU',         'Fuentes de poder'                 WHERE NOT EXISTS (SELECT 1 FROM categories WHERE name = 'PSU');

-- CPU: AMD Ryzen 7 7700X  (socket AM5)
INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
SELECT c.id, b.id, 'CPU-AMD-7700X', 'AMD Ryzen 7 7700X', 'Procesador de 8 nucleos para socket AM5', 1499.90,
       '{"socket":"AM5","cores":8,"threads":16,"tdp":105,"integrated_graphics":true}'::jsonb,
       'https://example.com/images/ryzen-7700x.jpg'
FROM categories c CROSS JOIN brands b
WHERE c.name = 'CPU' AND b.name = 'AMD'
  AND NOT EXISTS (SELECT 1 FROM products WHERE sku = 'CPU-AMD-7700X');

-- Motherboard: ASUS Prime B650  (socket AM5, DDR5)
INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
SELECT c.id, b.id, 'MB-ASUS-AM5-B650', 'ASUS Prime B650', 'Motherboard compatible con socket AM5 y DDR5', 899.90,
       '{"socket":"AM5","memory_type":"DDR5","chipset":"B650"}'::jsonb,
       'https://example.com/images/asus-b650.jpg'
FROM categories c CROSS JOIN brands b
WHERE c.name = 'Motherboard' AND b.name = 'ASUS'
  AND NOT EXISTS (SELECT 1 FROM products WHERE sku = 'MB-ASUS-AM5-B650');

-- GPU: NVIDIA RTX 4070 Ti  (recommended_psu_watts: 700)
INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
SELECT c.id, b.id, 'GPU-NV-4070TI', 'NVIDIA GeForce RTX 4070 Ti', 'GPU de alto rendimiento con 12 GB GDDR6X', 3299.90,
       '{"vram_gb":12,"memory_type":"GDDR6X","recommended_psu_watts":700,"length_mm":336}'::jsonb,
       'https://example.com/images/rtx-4070ti.jpg'
FROM categories c CROSS JOIN brands b
WHERE c.name = 'GPU' AND b.name = 'NVIDIA'
  AND NOT EXISTS (SELECT 1 FROM products WHERE sku = 'GPU-NV-4070TI');

-- RAM: Corsair Vengeance DDR5 32 GB
INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
SELECT c.id, b.id, 'RAM-COR-DDR5-32', 'Corsair Vengeance DDR5 32GB', 'Kit de 2x16 GB DDR5-6000', 549.90,
       '{"memory_type":"DDR5","capacity_gb":32,"speed_mhz":6000,"modules":2}'::jsonb,
       'https://example.com/images/corsair-ddr5.jpg'
FROM categories c CROSS JOIN brands b
WHERE c.name = 'RAM' AND b.name = 'Corsair'
  AND NOT EXISTS (SELECT 1 FROM products WHERE sku = 'RAM-COR-DDR5-32');

-- PSU: Corsair RM850x 850W
INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
SELECT c.id, b.id, 'PSU-COR-RM850X', 'Corsair RM850x 850W', 'Fuente modular 80+ Gold 850 W', 449.90,
       '{"wattage":850,"efficiency":"80+ Gold","modular":true}'::jsonb,
       'https://example.com/images/corsair-rm850x.jpg'
FROM categories c CROSS JOIN brands b
WHERE c.name = 'PSU' AND b.name = 'Corsair'
  AND NOT EXISTS (SELECT 1 FROM products WHERE sku = 'PSU-COR-RM850X');
