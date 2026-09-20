import Fastify from "fastify";
import cors from "@fastify/cors";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import { Pool } from "pg";

const pool = new Pool({
  host: process.env.POSTGRES_HOST || process.env.DB_HOST || "postgres",
  port: parseInt(process.env.POSTGRES_PORT || process.env.DB_PORT || "5432"),
  database:
    process.env.POSTGRES_DB || process.env.DB_NAME || "hardtech_catalog",
  user: process.env.POSTGRES_USER || process.env.DB_USER || "hardtech",
  password:
    process.env.POSTGRES_PASSWORD || process.env.DB_PASSWORD || "hardtech",
});

const app = Fastify({ logger: true });

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
            id: { type: "string", description: "ID del producto" },
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
        body: {
          type: "object",
          required: ["category_id", "brand_id", "sku", "name", "price"],
          properties: {
            category_id: { type: "integer" },
            brand_id: { type: "integer" },
            sku: { type: "string" },
            name: { type: "string" },
            description: { type: "string" },
            price: { type: "number" },
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
        params: { type: "object", properties: { id: { type: "string" } } },
        body: {
          type: "object",
          properties: {
            name: { type: "string" },
            description: { type: "string" },
            price: { type: "number" },
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
      const { name, description, price, specs, image_url, is_active } =
        req.body as Record<string, unknown>;
      const { rowCount } = await pool.query(
        `UPDATE products SET name=$1, description=$2, price=$3, specs=$4, image_url=$5, is_active=$6 WHERE id=$7`,
        [
          name,
          description,
          price,
          JSON.stringify(specs || {}),
          image_url,
          is_active,
          id,
        ],
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
        params: { type: "object", properties: { id: { type: "string" } } },
        response: {
          200: { type: "object", properties: { deleted: { type: "boolean" } } },
        },
      },
    },
    async (req, reply) => {
      const id = parseInt(req.params.id);
      await pool.query("UPDATE products SET is_active=FALSE WHERE id=$1", [id]);
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
