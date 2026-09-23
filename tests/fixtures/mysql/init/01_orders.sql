CREATE TABLE orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    status VARCHAR(20) NOT NULL DEFAULT 'RESERVING',
    subtotal DECIMAL(16,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    tax DECIMAL(16,2) NOT NULL DEFAULT 0 CHECK (tax >= 0),
    shipping_cost DECIMAL(16,2) NOT NULL DEFAULT 0 CHECK (shipping_cost >= 0),
    total_amount DECIMAL(16,2) NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    request_key VARCHAR(100) UNIQUE,
    request_hash CHAR(64),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (status IN ('RESERVING','PENDING','PAID','SHIPPED','CANCELLED')),
    INDEX idx_orders_status(status),
    INDEX idx_orders_created(created_at)
);
CREATE TABLE order_items (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL CHECK (product_id > 0),
    product_sku VARCHAR(80) NOT NULL,
    product_name VARCHAR(180) NOT NULL,
    product_category VARCHAR(120) NOT NULL,
    quantity INT NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(16,2) NOT NULL CHECK (unit_price >= 0),
    subtotal DECIMAL(16,2) NOT NULL CHECK (subtotal >= 0),
    CONSTRAINT fk_order_items_order FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE,
    INDEX idx_items_product(product_id)
);
