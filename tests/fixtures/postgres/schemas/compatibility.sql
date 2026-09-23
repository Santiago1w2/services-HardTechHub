SET ROLE compatibility_app;
CREATE TABLE compatibility_checks (
    id BIGSERIAL PRIMARY KEY,
    compatible BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE compatibility_components (
    check_id BIGINT NOT NULL REFERENCES compatibility_checks(id) ON DELETE CASCADE,
    component_type VARCHAR(30) NOT NULL,
    product_id BIGINT NOT NULL CHECK (product_id > 0),
    PRIMARY KEY(check_id, component_type)
);
CREATE TABLE compatibility_messages (
    check_id BIGINT NOT NULL REFERENCES compatibility_checks(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY(check_id, position)
);
CREATE INDEX idx_checks_created ON compatibility_checks(created_at);
CREATE INDEX idx_components_product ON compatibility_components(product_id);
RESET ROLE;
