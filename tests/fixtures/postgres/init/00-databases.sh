#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v inv_password="$INVENTORY_POSTGRES_PASSWORD" -v compat_password="$COMPATIBILITY_POSTGRES_PASSWORD" <<'SQL'
CREATE ROLE inventory_app LOGIN PASSWORD :'inv_password';
CREATE ROLE compatibility_app LOGIN PASSWORD :'compat_password';
CREATE DATABASE hardtech_inventory OWNER inventory_app;
CREATE DATABASE hardtech_compatibility OWNER compatibility_app;
REVOKE CONNECT ON DATABASE hardtech_inventory FROM PUBLIC;
REVOKE CONNECT ON DATABASE hardtech_compatibility FROM PUBLIC;
GRANT CONNECT ON DATABASE hardtech_inventory TO inventory_app;
GRANT CONNECT ON DATABASE hardtech_compatibility TO compatibility_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hardtech_inventory -f /schemas/inventory.sql
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname hardtech_compatibility -f /schemas/compatibility.sql
