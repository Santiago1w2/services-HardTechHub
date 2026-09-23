import Fastify from "fastify";
import cors from "@fastify/cors";
import swagger from "@fastify/swagger";
import swaggerUi from "@fastify/swagger-ui";
import { repository } from "./repository";
import { registerAdminCatalogRoutes } from "./adminRoutes";

const app = Fastify({ logger: true });
app.addHook("onClose", async () => repository.close());

app.setErrorHandler((error, _req, reply) => {
  if (String(error.code) === "121") return reply.status(400).send({ detail: "Invalid product data" });
  if (error.name.startsWith("Mongo") && String(error.code) !== "11000") return reply.status(503).send({ detail: "MongoDB unavailable" });
  if (error.validation) return reply.status(400).send({ detail: "Invalid request" });
  if (String(error.code) === "11000") return reply.status(409).send({ detail: "Product already exists" });
  if (error.statusCode && error.statusCode < 500) {
    return reply.status(error.statusCode).send({ detail: "Invalid request" });
  }
  return reply.status(500).send({ detail: "Catalog Service error" });
});

const start = async () => {
  // 1. CORS
  await app.register(cors, {
    origin: (process.env.CORS_ORIGINS || "http://localhost:5173,http://localhost:4173")
      .split(",").map((origin) => origin.trim()).filter(Boolean),
    allowedHeaders: ["Content-Type"],
    credentials: false,
    methods: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
  });

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


  await app.register(swaggerUi, {
    routePrefix: "/docs",
    uiConfig: { docExpansion: "list" },
  });


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
    async () => {
      await repository.ping();
      return ({
      service: "catalog-service",
      status: "healthy",
      version: "1.0.0",
    }); },
  );

  const productSchema = {
    type: "object",
    properties: {
      id: { type: "integer" },
      sku: { type: "string", minLength: 1, maxLength: 80 },
      name: { type: "string", minLength: 1, maxLength: 180 },
      description: { type: "string" },
      price: { type: "string" },
      specs: { type: "object", additionalProperties: true },
      specifications: { type: "object", additionalProperties: true },
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
      return reply.send(await repository.list());
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
      if (!Number.isSafeInteger(id))
        return reply.status(400).send({ detail: "Invalid product id" });
      const product = await repository.get(id);
      if (!product) return reply.status(404).send({ detail: "Product not found" });
      return reply.send(product);
    },
  );

  app.post(
    "/api/products",
    {
      schema: {
        tags: ["products"],
        summary: "Crear un producto",
        body: {
          type: "object",
          additionalProperties: false,
          required: ["category_id", "brand_id", "sku", "name", "price"],
          properties: {
            category_id: { type: "integer", minimum: 1 },
            brand_id: { type: "integer", minimum: 1 },
            sku: { type: "string", minLength: 1, maxLength: 80 },
            name: { type: "string", minLength: 1, maxLength: 180 },
            description: { type: "string" },
            price: { type: "number", minimum: 0, maximum: 999999999999.99, multipleOf: 0.01 },
            specs: { type: "object", additionalProperties: true },
            specifications: { type: "object", additionalProperties: true },
            image_url: { type: "string" },
          },
        },
        response: {
          201: {
            type: "object",
            properties: {
              id: { type: "integer" },
              sku: { type: "string", minLength: 1, maxLength: 80 },
              name: { type: "string", minLength: 1, maxLength: 180 },
              price: { type: "string" },
            },
          },
        },
      },
    },
    async (req, reply) => {
      const product = await repository.create(req.body as Record<string, unknown>);
      if (!product) return reply.status(400).send({ detail: "Invalid category or brand" });
      return reply.status(201).send(product);
    },
  );

  app.put<{ Params: { id: string } }>(
    "/api/products/:id",
    {
      schema: {
        tags: ["products"],
        summary: "Actualizar un producto",
        params: { type: "object", properties: { id: { type: "string", pattern: "^[1-9][0-9]*$" } } },
        body: {
          type: "object",
          minProperties: 1,
          additionalProperties: false,
          properties: {
            name: { type: "string", minLength: 1, maxLength: 180 },
            description: { type: "string" },
            price: { type: "number", minimum: 0, maximum: 999999999999.99, multipleOf: 0.01 },
            specs: { type: "object", additionalProperties: true },
            specifications: { type: "object", additionalProperties: true },
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
      if (!Number.isSafeInteger(id)) return reply.status(400).send({ detail: "Invalid product id" });
      const rowCount = await repository.update(id, req.body as Record<string, unknown>);
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
        params: { type: "object", properties: { id: { type: "string", pattern: "^[1-9][0-9]*$" } } },
        response: {
          200: { type: "object", properties: { deleted: { type: "boolean" } } },
        },
      },
    },
    async (req, reply) => {
      const id = parseInt(req.params.id);
      if (!Number.isSafeInteger(id)) return reply.status(400).send({ detail: "Invalid product id" });
      const rowCount = await repository.update(id, { is_active: false });
      if (rowCount === 0) return reply.status(404).send({ detail: "Product not found" });
      return reply.send({ deleted: true });
    },
  );

  registerAdminCatalogRoutes(app, repository);

  // 5. Inicializar Swagger y levantar el servidor
  await repository.connect();
  await app.ready();
  const PORT = parseInt(process.env.PORT || "8002");
  try {
    await app.listen({ port: PORT, host: "0.0.0.0" });
  } catch (err) {
    app.log.error(err);
    process.exit(1);
  }
};

start().catch((error) => { app.log.error(error); process.exit(1); });
