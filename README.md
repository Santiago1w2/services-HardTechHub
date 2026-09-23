# HardTechHub: microservicios de negocio

```text
HardTechHub
├── catalog-service       → PostgreSQL / hardtech_catalog
├── inventory-service     → MongoDB / hardtech_inventory
├── order-service         → MySQL / hardtech_orders
├── compatibility-service → sin base de datos (stateless, llama a catalog)
└── analytics-service     → S3 → Glue Data Catalog → Athena
```

Python/FastAPI en catálogo, pedidos, inventario y analytics; Java 21/Spring Boot
en compatibilidad. Las APIs de negocio son públicas. CORS admite únicamente los
orígenes configurados.

Cada servicio tiene su propia base y credenciales técnicas. PostgreSQL comparte
instancia local, pero separa bases, propietarios y permisos CONNECT. Ningún servicio
consulta directamente la base de otro. Las FK solamente relacionan tablas internas.

## Arranque (PowerShell)

Desde la carpeta del repositorio de infraestructura:

```powershell
# Solo si aún no existe .env:
Copy-Item .env.example .env
docker compose config --quiet
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose ps
```

Después, desde este repositorio:

```powershell
# Solo si aún no existe .env; coordinar contraseñas con infraestructura:
Copy-Item .env.example .env
docker compose config --quiet
docker compose build
docker compose up -d --wait --wait-timeout 180
docker compose ps
docker compose logs --tail 100
```

## Máquinas separadas: EC2 Database y EC2 de servicios

Cada Compose crea su propia red Docker local. No hay red externa compartida.
En Database ejecutar el Compose de infraestructura; en PROD ejecutar este Compose.

Antes de arrancar, copiar .env.example a .env sin sobrescribir uno existente y configurar:
- DATABASE_HOST: IP privada o DNS de Database, sin http://.
- CATALOG_POSTGRES_PASSWORD, MYSQL_PASSWORD, INVENTORY_MONGO_URI: deben coincidir
  con las cuentas técnicas creadas en infraestructura (`catalog_app`, `orders_app`,
  `inventory_app`). Puerto por defecto: PostgreSQL 5432, MySQL 3306, MongoDB 27017.
- CORS_ORIGINS: origen real del frontend.

DATABASE_HOST es el valor común; POSTGRES_HOST y MYSQL_HOST permiten excepciones.
No usar localhost, postgres, mysql ni mongodb para una base en otra máquina.
Si se cambian puertos, actualizar ambos lados y los Security Groups; el puerto
interno del contenedor no cambia.

La plantilla AWS del workspace permite bases desde SG-PROD y comunicación privada
entre PROD1/PROD2 en 8002–8006. Aplicar la plantilla actualizada en AWS.
No es necesario desplegar ambos Compose en cada EC2.

Si todos los microservicios corren juntos, conservar sus URLs por nombre Docker.
Si se reparten, configurar CATALOG_SERVICE_URL, INVENTORY_SERVICE_URL y
ORDER_SERVICE_URL con IP privada y puerto de su EC2. Levantar solo los servicios
asignados con --no-deps, ya que depends_on solo comprueba contenedores locales:

```bash
# Ejemplo de reparto, no obligatorio:
# PROD1: catálogo e inventario
docker compose up -d --build --no-deps --wait catalog-service inventory-service
# PROD2: configurar las URLs de catálogo/inventario con la IP privada de PROD1
docker compose up -d --build --no-deps --wait order-service compatibility-service analytics-service
```

Para todos los servicios en una sola EC2, usar el arranque normal descrito arriba.
docker-compose.aws.yml únicamente monta un perfil AWS para desarrollo local;
en EC2 utilizar el rol IAM de ejecución.

Los volúmenes de negocio son nuevos. Los scripts init se ejecutan solamente con
volúmenes vacíos. Los datos previos no se destruyen ni se importan automáticamente.
No utilizar `down --volumes` contra datos que se quieran conservar.
El catálogo incluye los cinco componentes iniciales existentes. Inventario y pedidos
comienzan vacíos: registrar stock antes de comprar.

## Configuración

Consultar `.env.example`. Los nombres de las bases y las cuentas técnicas están
definidos coherentemente en Compose e init scripts.

- Catálogo: `POSTGRES_HOST` (o `DATABASE_HOST`), `POSTGRES_PORT`,
  `CATALOG_POSTGRES_PASSWORD`; cuenta `catalog_app`, base `hardtech_catalog`.
- Pedidos: `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_PASSWORD`; cuenta `orders_app`,
  base `hardtech_orders`.
- Inventario: `INVENTORY_MONGO_URI` (cuenta `inventory_app`, base `hardtech_inventory`).
- Compatibilidad: no usa base; solo `SERVER_PORT` y `CATALOG_SERVICE_URL`.
- Comunicación: `CATALOG_SERVICE_URL`, `INVENTORY_SERVICE_URL`,
  `ORDER_SERVICE_URL`. Dentro de Docker no apuntan a localhost.
- Frontend: `CORS_ORIGINS`; puertos publicados `*_SERVICE_PORT`.
- AWS: `AWS_DEFAULT_REGION`, `S3_BUCKET`, `S3_EVENTS_PREFIX`,
  `S3_DATA_PREFIX`, `GLUE_DATABASE`, `ATHENA_WORKGROUP`,
  `ATHENA_OUTPUT_LOCATION`, `ATHENA_QUERY_TIMEOUT_SECONDS`.

AWS usa la cadena estándar de credenciales boto3 (por ejemplo, rol IAM de EC2/ECS).
Para un perfil local, usar opcionalmente:

```powershell
$env:AWS_CONFIG_DIR = "$env:USERPROFILE/.aws"
$env:AWS_PROFILE = "default"
docker compose -f docker-compose.yml -f docker-compose.aws.yml up -d analytics-service
```

No incluir credenciales AWS en imágenes o archivos versionados.

## Catálogo (8002)

- `GET /api/products`: activos; `GET /api/products/{id}`: detalle.
- `POST /api/products`, `PUT /api/products/{id}`, `DELETE /api/products/{id}`.
  DELETE desactiva el producto.
- `GET /api/categories`, `GET /api/brands`.
- `GET /api/admin/products?page=1&limit=20&status=all&q=Ryzen`:
  paginación, filtros `category_id`, `brand_id`, estados active/inactive/all.
  El segmento admin se conserva como contrato de la pantalla de gestión.
- `GET /health` comprueba PostgreSQL; Swagger en `/docs`.

PostgreSQL conserva `brands`, `categories` y `products` (con `specs JSONB`).
Las validaciones de entrada devuelven 400 (no 422): el handler convierte los
errores de Pydantic/FastAPI. SKU tiene índice único. `specifications` es el
documento flexible persistido; `specs` se acepta y devuelve como alias.
Precios de entrada numéricos y de salida como texto decimal de dos cifras.

```json
{
  "category_id": 1,
  "brand_id": 1,
  "sku": "CPU-DEMO",
  "name": "CPU demo",
  "price": 100,
  "specifications": {"socket": "AM5", "cores": 8}
}
```

## Inventario (8006)

- `GET /inventory`, `GET /inventory/{product_id}`.
- `POST /inventory`: `{"product_id":1,"stock":10,"reorder_point":2}`.
- `PUT /inventory/{product_id}`: stock físico absoluto y punto de reposición.
- `POST /inventory/reserve`, `/inventory/release`, `/inventory/confirm`.
- `GET /inventory/movements?after_id=0&limit=100`, `GET /health`.

Las operaciones de reserva reciben:

```json
{"order_id": 1, "items": [{"product_id": 1, "quantity": 2}]}
```

`available_stock = stock - reserved_stock`. MongoDB mantiene el id numérico de
producto como `_id`; los movimientos reciben `_id` numérico incremental (contador
`counters`), lo que habilita la paginación `after_id` de analytics. La reserva usa
`$inc` atómico con condición `$expr` sobre el stock disponible y revierte los
cambios aplicados si algún ítem del lote falla. Un lote completo se confirma o revierte.
Cantidades repetidas del mismo producto se suman.

`inventory_reservations` registra el pedido externo, sus cantidades y estado.
Repetir la misma operación no duplica movimientos. Cambiar cantidades durante
un reintento devuelve 409. Liberar antes de reservar registra una cancelación
que bloquea reservas tardías. CONFIRM reduce stock y reserva; RELEASE solo la reserva.
`inventory_movements` registra STOCK_IN, RESERVE, RELEASE, SALE y ADJUSTMENT con
`order_id` opcional. Los IDs de producto y pedido son referencias externas sin FK
a otro servicio. Los errores de validación conservan el 422 estándar de FastAPI.

## Pedidos (8003)

- `POST /api/orders`: `{"items":[{"product_id":1,"quantity":2}]}`.
- `GET /api/orders`, `GET /api/orders/{order_id}`.
- `GET /api/admin/orders`: página/límite, estado, order_id, date_from/date_to.
- `PATCH /api/orders/{order_id}/status`: `{"status":"PAID"}`.
- `POST /api/orders/{order_id}/retry`: recuperar una reserva pendiente.
- `GET /api/exports/orders?after_id=0&limit=500`: exportación paginada para analytics.
- `GET /health`, `/docs`.

MySQL conserva `orders.id BIGINT AUTO_INCREMENT`; la API lo devuelve como
`order_id`. Los pedidos guardan líneas con precio, SKU, nombre y categoría
obtenidos por HTTP del catálogo. Se conserva el cálculo existente:
subtotal + IGV del 18% + envío de 25.

Flujo: se persiste RESERVING → se reserva por HTTP → PENDING. Sin stock, el
pedido queda CANCELLED y la creación devuelve 409. Si se pierde la respuesta
de inventario, devuelve 503 con order_id y permite recuperar el mismo pedido.
PENDING → PAID confirma la venta; PENDING/RESERVING → CANCELLED libera la reserva.
PAID → SHIPPED no vuelve a descontar stock. Otros cambios de estado se rechazan.
La cancelación posterior a la venta requeriría un flujo de devolución separado.

Para reintentar una creación sin duplicar el pedido, enviar el mismo encabezado
`Idempotency-Key` y el mismo cuerpo. La clave es opcional y no representa una
credencial. Si se pierde la respuesta inicial y no se envió clave, consultar
el listado antes de crear otro pedido. Las transiciones entre servicios son
recuperables, no una transacción distribuida. Un pedido RESERVING requiere
reintentar o cancelar; no hay un worker automático de reconciliación.

## Compatibilidad (8004)

`POST /api/compatibility/check` conserva las reglas CPU/socket, RAM/memoria y
GPU/fuente de poder. Consulta productos por HTTP al catálogo. No tiene base de
datos propia ni historial persistido: cada `check` es stateless. `GET /health`
verifica solo que el proceso responde.

```json
{"components":[{"type":"cpu","product_id":1},{"type":"motherboard","product_id":2}]}
```

## Analytics (8005)

La plantilla de infraestructura `aws/analytics.cloudformation.json` configura
S3, Glue, Athena y una política IAM que se puede adjuntar al rol de ejecución.

1. `POST /api/analytics/refresh` obtiene pedidos y movimientos mediante APIs
   paginadas y conserva los eventos PRODUCT_VIEW que ya se leían desde S3.
2. Publica JSONL en `analytics/business/current.jsonl`. Una nueva instantánea
   reemplaza el mismo objeto para evitar duplicar ventas al reintentar.
3. Glue expone la tabla externa `business_snapshot`; no requiere crawler.
4. Las consultas de negocio se ejecutan por Athena usando boto3, con paginación,
   timeout, cancelación y errores sanitizados.

Endpoints: `/api/analytics/summary`, `/top-products`, `/top-categories`,
`/trends`, `/inventory-movements`, `/events/count` y `/top-views`,
todos bajo `/api/analytics`. Top-products ahora mide unidades vendidas;
top-views conserva el análisis de visualizaciones. Solo PAID/SHIPPED cuentan
como ventas. Revenue de pedidos incluye impuestos/envío; ingresos por producto
y categoría corresponden al subtotal de sus líneas. Las tendencias se agrupan
por fecha de creación del pedido.

`/health` comprueba el proceso. `/health/aws` comprueba bucket, tabla y workgroup.
Un /health exitoso no certifica acceso a AWS. Sin configuración AWS las consultas
devuelven 503, mientras los servicios operacionales funcionan normalmente.

La actualización es explícita y eventual: ejecutar refresh antes de consultar
cambios recientes o programarlo externamente. No hay CDC ni streaming.
La instantánea se publica con un PUT atómico, pero las APIs se leen en momentos
distintos; no es una foto transaccional global. La implementación local carga la
instantánea en memoria y ejecuta un único proceso de exportación; para grandes
volúmenes se debe ampliar la ingesta. Mantener los prefijos por defecto de la
plantilla; si se cambian, actualizar también Location de Glue y permisos IAM.

## Pruebas aisladas

Desde este repositorio:

```powershell
docker compose -p hardtech-business-test -f tests/compose.yml config --quiet
docker compose -p hardtech-business-test -f tests/compose.yml build
docker compose -p hardtech-business-test -f tests/compose.yml up -d --no-build --wait --wait-timeout 180 mongodb postgres mysql catalog-service inventory-service order-service compatibility-service analytics-service
docker compose -p hardtech-business-test -f tests/compose.yml run --rm --no-deps -T runner
docker compose -p hardtech-business-test -f tests/compose.yml logs --tail 100
docker compose -p hardtech-business-test -f tests/compose.yml down --volumes --remove-orphans
```

Solo este entorno de pruebas usa bases temporales tmpfs. Sus fixtures son copias
de los scripts de infraestructura. No usa secretos de AWS; S3/Athena se simulan.
La suite cubre persistencia PostgreSQL/MongoDB, inventario concurrente,
restricciones, reservas idempotentes, recuperación de pedidos y motor
de compatibilidad stateless. La construcción de compatibilidad ejecuta
también Maven/JUnit. GitHub Actions ejecuta estos mismos pasos y conserva
logs si hay errores.