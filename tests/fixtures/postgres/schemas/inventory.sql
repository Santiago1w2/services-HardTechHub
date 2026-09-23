SET ROLE inventory_app;
CREATE TABLE inventory (
    product_id BIGINT PRIMARY KEY CHECK (product_id > 0),
    stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    reserved_stock INTEGER NOT NULL DEFAULT 0 CHECK (reserved_stock >= 0),
    reorder_point INTEGER NOT NULL DEFAULT 0 CHECK (reorder_point >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (reserved_stock <= stock)
);
CREATE TABLE inventory_reservations (
    order_id BIGINT PRIMARY KEY CHECK (order_id > 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('RESERVED','RELEASED','CONFIRMED')),
    items JSONB NOT NULL CHECK (jsonb_typeof(items) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE inventory_movements (
    id BIGSERIAL PRIMARY KEY,
    product_id BIGINT NOT NULL REFERENCES inventory(product_id),
    order_id BIGINT,
    movement_type VARCHAR(20) NOT NULL CHECK (movement_type IN ('STOCK_IN','RESERVE','RELEASE','SALE','ADJUSTMENT')),
    quantity INTEGER NOT NULL CHECK (quantity <> 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (movement_type = 'ADJUSTMENT' OR quantity > 0)
);
CREATE INDEX idx_movements_product_date ON inventory_movements(product_id, created_at);
CREATE INDEX idx_movements_order ON inventory_movements(order_id);
CREATE INDEX idx_reservations_status ON inventory_reservations(status);
RESET ROLE;
