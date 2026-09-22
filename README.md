# Microservicios HardTechHub

## Identity: MongoDB
- Implementación completa: identity-service/app/main.py.
- Dependencias: identity-service/requirements.txt.
- Variables: identity-service/.env.example.
- Fragmento independiente: identity-service/docker-compose.example.yml.
- MongoDB y red local: ../../infrastructure/docker-compose.yml.
- Stack integrado: ../../docker-compose.yml (ejecutar desde la raíz del proyecto).

Se eliminaron del servicio Identity el cliente y dependencias AWS, las tablas,
los scans y la normalización de atributos de DynamoDB. PyMongo reutiliza un
cliente por proceso, consulta por índices y cierra el cliente al apagar FastAPI.
El lifespan verifica conexión, crea la colección si falta y asegura índices
únicos de email y user_id. Un error de conexión impide un arranque falso; una
caída posterior produce HTTP 503 sin detalles internos.

Las rutas y estructuras de respuesta se mantienen. Los nuevos user_id son UUID
para evitar colisiones de correos con el mismo prefijo. No se migran datos antiguos
automáticamente. Registro exige al menos 8 caracteres y máximo 72 bytes UTF-8;
login mantiene su modelo. JWT_SECRET es obligatorio (mínimo 32 bytes).
JWT_EXP_MINUTES debe ser positivo. No hay secreto JWT predeterminado.

## Arranque local integrado
Desde la raíz del proyecto:
1. Copiar microservicios/services-HardTechHub/identity-service/.env.example a .env.
2. Generar JWT_SECRET con python -c "import secrets; print(secrets.token_urlsafe(48))"
   y colocar el resultado en .env.
3. Ejecutar docker compose up --build -d.

Como alternativa, para bases y servicio Identity por separado:
1. docker compose -f infrastructure/docker-compose.yml up -d
2. Crear .env dentro de identity-service usando .env.example y configurar el secreto.
3. Desde identity-service: docker compose --env-file .env -f docker-compose.example.yml up --build -d.

Usar una sola modalidad local a la vez para evitar conflictos de puertos y red.
Los usuarios se registran siempre como customer. Para tareas administrativas,
asignar el rol admin a un usuario mediante una operación de administración de
MongoDB; no existe registro público de administradores.

## Permisos entre microservicios
Orders, Catalog y Analytics envían Authorization a IDENTITY_SERVICE_URL/api/auth/me;
Identity valida HS256, expiración y usuario.
Las URLs se configuran por entorno para DNS privado o IP privada en AWS.
401: token ausente/inválido; 403: permisos insuficientes; 503: Identity no disponible.
No se comparte el secreto JWT con los servicios consumidores.

- Catálogo: GET público; POST, PUT y DELETE requieren admin.
- Pedidos: creación solo para el user_id autenticado; consulta por dueño o admin.
  Listado global y cambio de estado requieren admin.
- Analytics: consultas requieren admin.
- Compatibilidad: consulta pública con validación de componentes/especificaciones.

## Nube
MONGO_URI acepta cualquier DNS/IP privado; los valores locales son de desarrollo.
Configurar credenciales propias y direcciones privadas en despliegue. El Compose
integrado es para desarrollo; el fragmento Identity funciona con una base externa
ajustando MONGO_URI y la red del despliegue.

Analytics sigue usando boto3 porque consulta S3 real, no la base de usuarios.
Se eliminó su endpoint LocalStack predeterminado y las credenciales ficticias.
En AWS utiliza la cadena estándar de credenciales del SDK, por ejemplo el rol IAM
de la VM, con acceso al bucket. Localmente necesita credenciales AWS configuradas
en el contenedor para consultar estadísticas.

## Referencias
- FastAPI lifespan: https://fastapi.tiangolo.com/advanced/events/
- PyMongo MongoClient: https://www.mongodb.com/docs/languages/python/pymongo-driver/current/connect/mongoclient/
- índices: https://www.mongodb.com/docs/languages/python/pymongo-driver/current/indexes/

## Pruebas de regresión
Desde la raíz del repositorio de microservicios (services-HardTechHub):
1. docker compose -p hardtech-review -f tests/compose.yml build identity-service catalog-service order-service compatibility-service analytics-service
2. docker compose -p hardtech-review -f tests/compose.yml build runner
3. docker compose -p hardtech-review -f tests/compose.yml up -d --no-build --wait --wait-timeout 180 mongodb postgres mysql identity-service catalog-service order-service compatibility-service analytics-service
4. docker compose -p hardtech-review -f tests/compose.yml run --rm --no-deps -T runner
5. docker compose -p hardtech-review -f tests/compose.yml down --volumes --remove-orphans

Los esquemas y datos SQL para pruebas están en tests/fixtures. Son copias de los
esquemas de infraestructura; mantenerlas actualizadas cuando cambie la base.
La suite no necesita archivos fuera de este repositorio.

Las pruebas usan bases reales temporales en tmpfs, una red independiente y ningún
puerto publicado. S3 y fallos de infraestructura se simulan en las pruebas unitarias;
no se ejecutan operaciones en AWS. La suite cubre 22 casos de registro, JWT,
permisos, pedidos, catálogo, compatibilidad, MongoDB y lectura de eventos.

La construcción de Catalog informó 4 vulnerabilidades de dependencias (1 moderada,
3 altas). No se actualizó ese conjunto de dependencias en esta corrección.

## GitHub Actions
El workflow .github/workflows/microservices-ci.yml se ejecuta en cada push y pull
request, y permite ejecución manual desde Actions > Microservices CI.

Construye los cinco servicios (incluyendo las pruebas Maven de Compatibility),
levanta MongoDB, PostgreSQL y MySQL temporales y ejecuta los 22 casos de regresión.
Si falla una compilación o una prueba, la ejecución queda en rojo. Guarda los logs
como artefacto microservices-test-logs durante 7 días y limpia el entorno al terminar.
No necesita secretos de GitHub ni credenciales AWS: usa datos de prueba y simula S3.

Subir .github/workflows, tests/ (incluyendo fixtures) y los cinco directorios de
servicios. La carpeta services-HardTechHub debe ser la raíz del repositorio en
GitHub, tal como indica su .git local. Este workflow ejecuta pruebas; no despliega.
Si el repositorio tiene Actions deshabilitado, habilitarlo en Settings > Actions.

Referencia: https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
