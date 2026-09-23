const inventory = db.getSiblingDB("hardtech_inventory");
inventory.createUser({
  user: "inventory_app",
  pwd: process.env.INVENTORY_MONGO_PASSWORD,
  roles: [{ role: "readWrite", db: "hardtech_inventory" }]
});
inventory.createCollection("inventory", { validator: { $jsonSchema: {
  bsonType: "object",
  required: ["_id", "stock", "reserved_stock", "reorder_point"],
  properties: {
    _id: { bsonType: ["int", "long"], minimum: 1 },
    stock: { bsonType: ["int", "long"], minimum: 0 },
    reserved_stock: { bsonType: ["int", "long"], minimum: 0 },
    reorder_point: { bsonType: ["int", "long"], minimum: 0 }
  }
}}});
inventory.createCollection("inventory_movements", { validator: { $jsonSchema: {
  bsonType: "object",
  required: ["_id", "product_id", "movement_type", "quantity"],
  properties: {
    _id: { bsonType: ["int", "long"], minimum: 1 },
    product_id: { bsonType: ["int", "long"], minimum: 1 },
    movement_type: { enum: ["STOCK_IN", "RESERVE", "RELEASE", "SALE", "ADJUSTMENT"] },
    quantity: { bsonType: ["int", "long"] }
  }
}}});
inventory.createCollection("inventory_reservations", { validator: { $jsonSchema: {
  bsonType: "object",
  required: ["order_id", "status", "items"],
  properties: {
    order_id: { bsonType: ["int", "long"], minimum: 1 },
    status: { enum: ["RESERVED", "RELEASED", "CONFIRMED"] },
    items: { bsonType: "array" }
  }
}}});
inventory.createCollection("counters");
inventory.counters.insertOne({ _id: "movements", value: 0 });
inventory.inventory.createIndex({ updated_at: 1 });
inventory.inventory.createIndex({ stock: 1 });
inventory.inventory_movements.createIndex({ product_id: 1, created_at: 1 });
inventory.inventory_movements.createIndex({ order_id: 1 });
inventory.inventory_reservations.createIndex({ status: 1 });