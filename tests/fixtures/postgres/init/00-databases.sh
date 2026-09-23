#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v catalog_password="$CATALOG_POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE catalog_app LOGIN PASSWORD :'catalog_password';
CREATE DATABASE hardtech_catalog OWNER catalog_app;
REVOKE CONNECT ON DATABASE hardtech_catalog FROM PUBLIC;
GRANT CONNECT ON DATABASE hardtech_catalog TO catalog_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hardtech_catalog -f /schemas/catalog.sql