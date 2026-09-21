import Fastify from "fastify";
import cors from "@fastify/cors";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import { Pool } from "pg";

const pool = new Pool({
  host: process.env.POSTGRES_HOST,
  port: parseInt(process.env.POSTGRES_PORT ),
  database:
    process.env.POSTGRES_DB ",
  user: process.env.POSTGRES_USER,
  password:
    process.env.POSTGRES_PASSWORD,
});

const app = Fastify({ logger: { redact: ["req.headers.authorization"] } });

// Identity verifies the signature, expiration and current user roles.
app.addHook("preHandler", async (req, reply) => {
  if (!["POST", "PUT", "DELETE"].includes(req.method)) return;
  const authorization = req.headers.authorization;
  if (!authorization || !/^Bearer\s+\S+$/i.test(authorization)) {
    return reply.status(401).send({ detail: "Missing or invalid Bearer token" });
  }
  try {
    const identityUrl = (process.env.IDENTITY_SERVICE_URL).replace(/\/$/, "");
    const response = await fetch(identityUrl + "/api/auth/me", {
      headers: { Authorization: authorization },
      signal: AbortSignal.timeout(5000),
    });
    if ([401, 403, 404].includes(response.status)) {
      return reply.status(401).send({ detail: "Invalid or expired token" });
    }
    if (!response.ok) return reply.status(503).send({ detail: "Identity Service unavailable" });
    const user = await response.json() as { user_id?: string; roles?: unknown };
    if (typeof user.user_id !== "string" || !Array.isArray(user.roles) ||
        !user.roles.every((role: unknown) => typeof role === "string")) {
      return reply.status(502).send({ detail: "Invalid Identity Service response" });
    }
    if (!user.roles.includes("admin")) {
      return reply.status(403).send({ detail: "Admin role required" });
    }
  } catch {
    return reply.status(503).send({ detail: "Identity Service unavailable" });
  }
});

app.setErrorHandler((error, _req, reply) => {
  if (error.validation) return reply.status(400).send({ detail: "Invalid request" });
  if (error.code === "23505") return reply.status(409).send({ detail: "Product already exists" });
  if (error.code === "23503") return reply.status(400).send({ detail: "Invalid category or brand" });
  if (error.statusCode && error.statusCode < 500) {
    return reply.status(error.statusCode).send({ detail: "Invalid request" });
  }
  return reply.status(500).send({ detail: "Catalog Service error" });
});

const start = async () => {
  // 1. CORS
  await app.register(cors, {
    origin: "*",
    methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
  });

  // 2. Swagger Core (DEBE REGISTRARSE ANTES DE LAS RUTAS)
  await app.register(swagger, {
    openapi: {
      info: {
        title: "Catalog Service",
        description:
          "Gestión de productos, categorías y marcas de HardTech Hub",
        version: "1.0.0",
      },
      components: { securitySchemes: {
        bearerAuth: { type: "http", scheme: "bearer", bearerFormat: "JWT" },
      } },
      tags: [
        { name: "health", description: "Estado del servicio" },
        { name: "products", description: "Operaciones sobre productos" },
      ],
    },
  });

  // 3. Swagger UI
  await app.register(swaggerUi, {
    routePrefix: "/docs",
    uiConfig: { docExpansion: "list" },
  });

  // 4. DEFINICIÓN DE RUTAS (AHORA SÍ SWAGGER LAS DETECTA)

  // Health
  app.get(
    "/health",
    {
      schema: {
        tags: ["health"],
        summary: "Estado del servicio",
        response: {
          200: {
            type: "object",
            properties: {
              service: { type: "string" },
              status: { type: "string" },
              version: { type: "string" },
            },
          },
        },
      },
    },
    async () => ({
      service: "catalog-service",
      status: "healthy",
      version: "1.0.0",
    }),
  );

  // Products Schema
  const productSchema = {
    type: "object",
    properties: {
      id: { type: "integer" },
      sku: { type: "string" },
      name: { type: "string" },
      description: { type: "string" },
      price: { type: "string" },
      specs: { type: "object", additionalProperties: true },
      image_url: { type: "string" },
      category: { type: "string" },
      brand: { type: "string" },
    },
  };

  app.get(
    "/api/products",
    {
      schema: {
        tags: ["products"],
        summary: "Listar productos activos",
        response: { 200: { type: "array", items: productSchema } },
      },
    },
    async (_req, reply) => {
      const { rows } = await pool.query(`
        SELECT p.id, p.sku, p.name, p.description, p.price, p.specs, p.image_url,
               c.name AS category, b.name AS brand
        FROM products p
        JOIN categories c ON c.id = p.category_id
        JOIN brands b ON b.id = p.brand_id
        WHERE p.is_active = TRUE
        ORDER BY p.id ASC
      `);
      return reply.send(rows);
    },
  );

  app.get<{ Params: { id: string } }>(
    "/api/products/:id",
    {
      schema: {
        tags: ["products"],
        summary: "Obtener detalle de un producto",
        params: {
          type: "object",
          properties: {
            id: { type: "string", pattern: "^[1-9][0-9]*$", description: "ID del producto" },
          },
        },
        response: {
          200: {
            ...productSchema,
            properties: {
              ...productSchema.properties,
              is_active: { type: "boolean" },
              created_at: { type: "string" },
            },
          },
          404: { type: "object", properties: { detail: { type: "string" } } },
        },
      },
    },
    async (req, reply) => {
      const id = parseInt(req.params.id);
      if (isNaN(id))
        return reply.status(400).send({ detail: "Invalid product id" });
      const { rows } = await pool.query(
        `SELECT p.id, p.sku, p.name, p.description, p.price, p.specs, p.image_url,
                p.is_active, p.created_at, c.name AS category, b.name AS brand
         FROM products p
         JOIN categories c ON c.id = p.category_id
         JOIN brands b ON b.id = p.brand_id
         WHERE p.id = $1`,
        [id],
      );
      if (rows.length === 0)
        return reply.status(404).send({ detail: "Product not found" });
      return reply.send(rows[0]);
    },
  );

  app.post(
    "/api/products",
    {
      schema: {
        tags: ["products"],
        summary: "Crear un producto (admin)",
        security: [{ bearerAuth: [] }],
        body: {
          type: "object",
          required: ["category_id", "brand_id", "sku", "name", "price"],
          properties: {
            category_id: { type: "integer", minimum: 1 },
            brand_id: { type: "integer", minimum: 1 },
            sku: { type: "string" },
            name: { type: "string" },
            description: { type: "string" },
            price: { type: "number", minimum: 0 },
            specs: { type: "object", additionalProperties: true },
            image_url: { type: "string" },
          },
        },
        response: {
          201: {
            type: "object",
            properties: {
              id: { type: "integer" },
              sku: { type: "string" },
              name: { type: "string" },
              price: { type: "string" },
            },
          },
        },
      },
    },
    async (req, reply) => {
      const {
        category_id,
        brand_id,
        sku,
        name,
        description,
        price,
        specs,
        image_url,
      } = req.body as Record<string, unknown>;
      const { rows } = await pool.query(
        `INSERT INTO products (category_id, brand_id, sku, name, description, price, specs, image_url)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
         RETURNING id, sku, name, price`,
        [
          category_id,
          brand_id,
          sku,
          name,
          description,
          price,
          JSON.stringify(specs || {}),
          image_url,
        ],
      );
      return reply.status(201).send(rows[0]);
    },
  );

  app.put<{ Params: { id: string } }>(
    "/api/products/:id",
    {
      schema: {
        tags: ["products"],
        summary: "Actualizar un producto",
        security: [{ bearerAuth: [] }],
        params: { type: "object", properties: { id: { type: "string", pattern: "^[1-9][0-9]*$" } } },
        body: {
          type: "object",
          minProperties: 1,
          additionalProperties: false,
          properties: {
            name: { type: "string" },
            description: { type: "string" },
            price: { type: "number", minimum: 0 },
            specs: { type: "object", additionalProperties: true },
            image_url: { type: "string" },
            is_active: { type: "boolean" },
          },
        },
        response: {
          200: { type: "object", properties: { updated: { type: "boolean" } } },
          404: { type: "object", properties: { detail: { type: "string" } } },
        },
      },
    },
    async (req, reply) => {
      const id = parseInt(req.params.id);
      const body = req.body as Record<string, unknown>;
      const fields = ["name", "description", "price", "specs", "image_url", "is_active"]
        .filter((field) => Object.prototype.hasOwnProperty.call(body, field));
      if (!fields.length) return reply.status(400).send({ detail: "No fields to update" });
      const values = fields.map((field) => field === "specs" ? JSON.stringify(body[field]) : body[field]);
      const assignments = fields.map((field, index) => field + "=$" + (index + 1)).join(", ");
      const { rowCount } = await pool.query(
        "UPDATE products SET " + assignments + " WHERE id=$" + (fields.length + 1),
        [...values, id],
      );
      if (rowCount === 0)
        return reply.status(404).send({ detail: "Product not found" });
      return reply.send({ updated: true });
    },
  );

  app.delete<{ Params: { id: string } }>(
    "/api/products/:id",
    {
      schema: {
        tags: ["products"],
        summary: "Desactivar un producto",
        security: [{ bearerAuth: [] }],
        params: { type: "object", properties: { id: { type: "string", pattern: "^[1-9][0-9]*$" } } },
        response: {
          200: { type: "object", properties: { deleted: { type: "boolean" } } },
        },
      },
    },
    async (req, reply) => {
      const id = parseInt(req.params.id);
      const { rowCount } = await pool.query("UPDATE products SET is_active=FALSE WHERE id=$1", [id]);
      if (rowCount === 0) return reply.status(404).send({ detail: "Product not found" });
      return reply.send({ deleted: true });
    },
  );

  // 5. Inicializar Swagger y levantar el servidor
  await app.ready();
  const PORT = parseInt(process.env.PORT || "8002");
  try {
    await app.listen({ port: PORT, host: "0.0.0.0" });
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
};

start();
