import type { FastifyInstance } from "fastify";
import type { CatalogRepository } from "./repository";

interface AdminProductsQuery {
  page: number;
  limit: number;
  status: "all" | "active" | "inactive";
  q?: string;
  category_id?: number;
  brand_id?: number;
}

export function registerAdminCatalogRoutes(app: FastifyInstance, repository: CatalogRepository) {
  const optionSchema = {
    type: "object",
    properties: { id: { type: "integer" }, name: { type: "string" } },
  };

  app.get("/api/categories", {
    schema: {
      tags: ["products"],
      summary: "Listar categorías",
      response: { 200: { type: "array", items: optionSchema } },
    },
  }, async () => repository.options("categories"));

  app.get("/api/brands", {
    schema: {
      tags: ["products"],
      summary: "Listar marcas",
      response: { 200: { type: "array", items: optionSchema } },
    },
  }, async () => repository.options("brands"));

  app.get<{ Querystring: AdminProductsQuery }>("/api/admin/products", {
    schema: {
      tags: ["products"],
      summary: "Listado administrativo paginado de productos",
      querystring: {
        type: "object",
        additionalProperties: false,
        properties: {
          page: { type: "integer", minimum: 1, maximum: 1000000, default: 1 },
          limit: { type: "integer", minimum: 1, maximum: 100, default: 20 },
          status: { type: "string", enum: ["all", "active", "inactive"], default: "all" },
          q: { type: "string", maxLength: 180 },
          category_id: { type: "integer", minimum: 1, maximum: 2147483647 },
          brand_id: { type: "integer", minimum: 1, maximum: 2147483647 },
        },
      },
      response: {
        200: {
          type: "object",
          properties: {
            page: { type: "integer" },
            limit: { type: "integer" },
            total: { type: "integer" },
            items: {
              type: "array",
              items: {
                type: "object",
                properties: {
                  id: { type: "integer" },
                  sku: { type: "string" },
                  name: { type: "string" },
                  description: { type: ["string", "null"] },
                  price: { type: "string" },
                  specs: { type: "object", additionalProperties: true },
                  image_url: { type: ["string", "null"] },
                  category: { type: "string" },
                  brand: { type: "string" },
                  is_active: { type: "boolean" },
                  created_at: { type: "string" },
                },
              },
            },
          },
        },
      },
    },
  }, async (req) => {
    const { page, limit, status, q, category_id, brand_id } = req.query;
    return repository.page({ page, limit, status, q, category_id, brand_id });
  });
}
