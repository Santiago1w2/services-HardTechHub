const catalog = db.getSiblingDB("hardtech_catalog");
catalog.createUser({
  user: "catalog_app", pwd: process.env.CATALOG_MONGO_PASSWORD,
  roles: [{ role: "readWrite", db: "hardtech_catalog" }]
});
catalog.createCollection("products", { validator: { $jsonSchema: {
  bsonType: "object",
  required: ["_id", "sku", "name", "price", "category", "brand", "specifications", "is_active"],
  properties: {
    _id: { bsonType: ["int", "long", "double"], minimum: 1 },
    sku: { bsonType: "string", minLength: 1, maxLength: 80 },
    name: { bsonType: "string", minLength: 1, maxLength: 180 },
    price: { bsonType: ["double", "int", "long", "decimal"], minimum: 0 },
    specifications: { bsonType: "object" },
    is_active: { bsonType: "bool" }
  }
}}});
catalog.products.createIndex({sku: 1}, {unique: true});
catalog.products.createIndex({is_active: 1, _id: 1});
catalog.products.createIndex({category_id: 1, brand_id: 1, _id: -1});
catalog.products.createIndex({price: 1});
catalog.createCollection("categories");
catalog.createCollection("brands");
catalog.createCollection("counters");
catalog.categories.createIndex({name: 1}, {unique: true});
catalog.brands.createIndex({name: 1}, {unique: true});
["CPU", "Motherboard", "GPU", "RAM", "PSU"].forEach((name, i) => catalog.categories.insertOne({_id: i+1, name}));
["AMD", "NVIDIA", "Corsair", "ASUS"].forEach((name, i) => catalog.brands.insertOne({_id: i+1, name}));
// Same initial hardware as the original catalogue.
const seeds = [
 [1,1,1,"CPU-AMD-7700X","AMD Ryzen 7 7700X",1499.90,{socket:"AM5",cores:8,threads:16,tdp:105,integrated_graphics:true}],
 [2,2,4,"MB-ASUS-AM5-B650","ASUS Prime B650",899.90,{socket:"AM5",memory_type:"DDR5",chipset:"B650"}],
 [3,3,2,"GPU-NV-4070TI","NVIDIA GeForce RTX 4070 Ti",3299.90,{vram_gb:12,memory_type:"GDDR6X",recommended_psu_watts:700,length_mm:336}],
 [4,4,3,"RAM-COR-DDR5-32","Corsair Vengeance DDR5 32GB",549.90,{memory_type:"DDR5",capacity_gb:32,speed_mhz:6000,modules:2}],
 [5,5,3,"PSU-COR-RM850X","Corsair RM850x 850W",449.90,{wattage:850,efficiency:"80+ Gold",modular:true}]
];
seeds.forEach(([id,category_id,brand_id,sku,name,price,specifications]) => {
 catalog.products.insertOne({_id:id,category_id,brand_id,sku,name,price,specifications,
  category:catalog.categories.findOne({_id:category_id}).name,
  brand:catalog.brands.findOne({_id:brand_id}).name,
  description:"", image_url:"", is_active:true, created_at:new Date(), updated_at:new Date()});
});
catalog.counters.insertOne({_id:"products",value:5});
