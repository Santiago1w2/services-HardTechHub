# HardTechHub: microservicios de negocio

```text
HardTechHub
├── catalog-service       → MongoDB / hardtech_catalog
├── inventory-service     → PostgreSQL / hardtech_inventory
├── order-service         → MySQL / hardtech_orders
├── compatibility-service → PostgreSQL / hardtech_compatibility
└── analytics-service     → S3 → Glue Data Catalog → Athena
```

Se conservan TypeScript/Fastify en catálogo, Python/FastAPI en pedidos y analytics,
y Java 21/Spring Boot en compatibilidad. Inventario utiliza el patrón Python/FastAPI.
Las APIs de negocio son públicas. CORS admite únicamente los orígenes configurados.

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

En este workspace los repositorios están en `infrastructure` y
`microservicios/services-HardTechHub`. La red compartida se llama
`hardtech-business`; infraestructura la crea y servicios la consume como externa.
No es necesario combinar ambos archivos Compose. Los DNS internos son `mongodb`,
`postgres`, `mysql` y los nombres de los microservicios.


### Alcance de la red y despliegue EC2

El arranque anterior supone que ambos Compose se ejecutan en el mismo host Docker.
`external: true` significa que infraestructura creó la red; no significa una VPC
ni una conexión entre servidores. Crear redes con el mismo nombre en distintas
EC2 no las comunica.

Para distribuir PROD1/PROD2 y Database en EC2 distintas se requiere una configuración
de despliegue que use la IP privada o DNS de Database y sus puertos publicados,
y URLs privadas para APIs ubicadas en otra EC2. Los puertos deben coincidir con
los Security Groups. Los puertos locales publicados (55432, 3307, 27018 por defecto)
son distintos de los internos (5432, 3306, 27017); no basta cambiar solo el host.
Cada EC2 puede conservar su red Docker local. Esta adaptación distribuida está
pendiente; el Compose actual valida la arquitectura en un único host.
`docker-compose.aws.yml` monta un perfil AWS para analytics en desarrollo local;
no configura por sí mismo el despliegue distribuido en EC2.

Los volúmenes de negocio son nuevos. Los scripts init se ejecutan solamente con
volúmenes vacíos. Los datos previos no se destruyen ni se importan automáticamente.
No utilizar `down --volumes` contra datos que se quieran conservar.
El catálogo incluye los cinco componentes iniciales existentes. Inventario y pedidos
comienzan vacíos: registrar stock antes de comprar.

## Configuración

Consultar `.env.example`. Los nombres de las bases y las cuentas técnicas están
definidos coherentemente en Compose e init scripts.

- Catálogo: `CATALOG_MONGO_URI` debe contener la contraseña URL-encoded de
  `CATALOG_MONGO_PASSWORD` de infraestructura, cuenta `catalog_app`.
- Inventario: `INVENTORY_POSTGRES_HOST`, `INVENTORY_POSTGRES_PASSWORD`;
  cuenta `inventory_app`.
- Compatibilidad: `COMPATIBILITY_POSTGRES_HOST`,
  `COMPATIBILITY_POSTGRES_PASSWORD`; cuenta `compatibility_app`.
- Pedidos: `MYSQL_HOST`, `MYSQL_PASSWORD`; cuenta `orders_app`.
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
- `GET /health` comprueba MongoDB; Swagger en `/docs`.

MongoDB contiene `products`, `categories`, `brands` y `counters`.
Se conservan IDs numéricos externos; MongoDB permite usarlos como `_id`.
`counters` asigna IDs mediante incremento atómico. SKU tiene índice único.
`specifications` es el documento flexible persistido. `specs` sigue aceptándose
y devolviéndose como alias para compatibilidad con clientes existentes.
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

`available_stock = stock - reserved_stock`. PostgreSQL impide stock negativo
y reservas superiores al stock. Se bloquean las filas de productos en orden
estable y se serializan las operaciones de un mismo pedido. Un lote completo
se confirma o revierte. Cantidades repetidas del mismo producto se suman.

`inventory_reservations` registra el pedido externo, sus cantidades y estado.
Repetir la misma operación no duplica movimientos. Cambiar cantidades durante
un reintento devuelve 409. Liberar antes de reservar registra una cancelación
que bloquea reservas tardías. SALE reduce stock y reserva; RELEASE solo la reserva.
`inventory_movements` registra STOCK_IN, RESERVE, RELEASE, SALE y ADJUSTMENT.
Los IDs de producto y pedido son referencias externas sin FK a otro servicio.

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
GPU/fuente de poder. Consulta productos por HTTP. Cada resultado válido se
guarda transaccionalmente en `compatibility_checks`, `compatibility_components`
y `compatibility_messages`. `GET /health` comprueba su propia base.

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
La suite cubre persistencia MongoDB, inventario concurrente, restricciones,
reservas idempotentes, recuperación de pedidos y aislamiento PostgreSQL.
La construcción de compatibilidad ejecuta también Maven/JUnit.
GitHub Actions ejecuta estos mismos pasos y conserva logs si hay errores.
