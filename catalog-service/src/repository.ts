import { MongoClient, Document } from "mongodb";

export class CatalogRepository {
  private client = new MongoClient(process.env.CATALOG_MONGO_URI || "mongodb://mongodb:27017", {
    serverSelectionTimeoutMS: 5000, connectTimeoutMS: 5000, socketTimeoutMS: 5000,
  });
  private db = this.client.db(process.env.CATALOG_MONGO_DB || "hardtech_catalog");
  private products = this.db.collection<Document>("products");
  async connect() { await this.client.connect(); await this.ping(); }
  async close() { await this.client.close(); }
  async ping() { await this.db.command({ ping: 1 }); }
  private view(product: Document) {
    const { _id, specifications, ...fields } = product;
    return { ...fields, id: _id, price: Number(product.price).toFixed(2),
      specs: specifications || {}, specifications: specifications || {},
      created_at: product.created_at?.toISOString() };
  }
  async list() {
    return (await this.products.find({ is_active: true }).sort({ _id: 1 }).toArray()).map(p => this.view(p));
  }
  async get(id: number) {
    const p = await this.products.findOne({ _id: id } as Document);
    return p ? this.view(p) : null;
  }
  async options(collection: string) {
    return (await this.db.collection(collection).find().sort({ name: 1, _id: 1 }).toArray())
      .map(p => ({ id: p._id, name: p.name }));
  }
  async create(body: Record<string, unknown>) {
    const category = await this.db.collection("categories").findOne({ _id: body.category_id } as Document);
    const brand = await this.db.collection("brands").findOne({ _id: body.brand_id } as Document);
    if (!category || !brand) return null;
    const counter = await this.db.collection<Document>("counters").findOneAndUpdate(
      { _id: "products" } as Document, { $inc: { value: 1 } }, { upsert: true, returnDocument: "after" });
    const { specs, specifications, ...fields } = body;
    const product = { ...fields, _id: counter!.value, category: category.name, brand: brand.name,
      specifications: specifications ?? specs ?? {}, is_active: true, created_at: new Date(), updated_at: new Date() };
    await this.products.insertOne(product as Document);
    return this.view(product);
  }
  async update(id: number, body: Record<string, unknown>) {
    const { specs, specifications, ...fields } = body;
    if (specifications !== undefined || specs !== undefined) fields.specifications = specifications ?? specs;
    return (await this.products.updateOne({ _id: id } as Document,
      { $set: { ...fields, updated_at: new Date() } })).matchedCount;
  }
  async page(query: {page: number; limit: number; status: string; q?: string; category_id?: number; brand_id?: number}) {
    const { page, limit, status, q, category_id, brand_id } = query;
    const filter: Document = {};
    if (status !== "all") filter.is_active = status === "active";
    if (category_id !== undefined) filter.category_id = category_id;
    if (brand_id !== undefined) filter.brand_id = brand_id;
    if (q?.trim()) {
      const pattern = q.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      filter.$or = ["name", "sku", "category", "brand"].map(field => ({ [field]: { $regex: pattern, $options: "i" } }));
    }
    const total = await this.products.countDocuments(filter);
    const items = (await this.products.find(filter).sort({ _id: -1 })
      .skip((page - 1) * limit).limit(limit).toArray()).map(p => this.view(p));
    return { items, page, limit, total };
  }
}
export const repository = new CatalogRepository();
