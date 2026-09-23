import importlib.util
from pathlib import Path
import sys
import time
import unittest
from uuid import uuid4
from urllib.parse import urlencode

import httpx

ROOT = Path(__file__).resolve().parents[1]


def load_service(name):
    package = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        package, ROOT / name / "app/main.py",
        submodule_search_locations=[str(ROOT / name / "app")])
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)
    return module


analytics = load_service("analytics-service")
orders = load_service("order-service")
athena = sys.modules["analytics_service.athena"]
pipeline = sys.modules["analytics_service.pipeline"]


def api(service, port, path, method="GET", data=None, headers=None):
    return httpx.request(method, f"http://{service}:{port}{path}", json=data,
                         headers=headers, timeout=30)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for service, port in [("catalog-service",8002),("inventory-service",8006),
                              ("order-service",8003),("compatibility-service",8004),("analytics-service",8005)]:
            for attempt in range(90):
                try:
                    if api(service,port,"/health").status_code==200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError(f"{service} not healthy")

    def product(self, specs=None, stock=100):
        result=api("catalog-service",8002,"/api/products","POST",{
            "category_id":1,"brand_id":1,"sku":"TEST-"+uuid4().hex,
            "name":"Test component","price":100,"specs":specs or {}})
        self.assertEqual(result.status_code,201,result.text)
        pid=result.json()["id"]
        if stock is not None:
            result=api("inventory-service",8006,"/inventory","POST",{"product_id":pid,"stock":stock})
            self.assertEqual(result.status_code,201,result.text)
        return pid

    def inventory(self,pid):
        result=api("inventory-service",8006,f"/inventory/{pid}")
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def reservation(self,pid,quantity=2):
        return {"order_id":1000000000000+int(uuid4().hex[:10],16),
                "items":[{"product_id":pid,"quantity":quantity}]}

    def operation(self,action,body,status=200):
        result=api("inventory-service",8006,"/inventory/"+action,"POST",body)
        self.assertEqual(result.status_code,status,result.text)
        return result

    def test_cors_allows_only_configured_frontend_origins(self):
        for service, port, path, method in [('catalog-service', 8002, '/api/products', 'POST'), ('inventory-service', 8006, '/inventory', 'POST'), ('order-service', 8003, '/api/orders/1/status', 'PATCH'), ('analytics-service', 8005, '/api/analytics/events/count', 'GET'), ('compatibility-service', 8004, '/api/compatibility/check', 'POST')]:
            with self.subTest(service=service):
                url = f'http://{service}:{port}{path}'
                headers = {'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': method, 'Access-Control-Request-Headers': 'content-type'}
                response = httpx.options(url, headers=headers, timeout=15)
                self.assertIn(response.status_code, (200, 204), response.text)
                self.assertEqual(response.headers.get('access-control-allow-origin'), headers['Origin'])
                self.assertNotEqual(response.headers.get('access-control-allow-credentials'), 'true')
                headers['Origin'] = 'https://untrusted.invalid'
                response = httpx.options(url, headers=headers, timeout=15)
                self.assertNotIn('access-control-allow-origin', response.headers)

    def test_catalog_options_use_database_ids(self):
        categories = api('catalog-service', 8002, '/api/categories')
        brands = api('catalog-service', 8002, '/api/brands')
        self.assertEqual(categories.status_code, 200)
        self.assertEqual(brands.status_code, 200)
        for options in (categories.json(), brands.json()):
            self.assertTrue(options)
            self.assertTrue(all((set(item) == {'id', 'name'} for item in options)))
        category, brand = (categories.json()[0], brands.json()[0])
        result = api('catalog-service', 8002, '/api/products', 'POST', {'category_id': category['id'], 'brand_id': brand['id'], 'sku': 'OPTIONS-' + uuid4().hex, 'name': 'Options product', 'price': 10})
        self.assertEqual(result.status_code, 201, result.text)
        product = api('catalog-service', 8002, f"/api/products/{result.json()['id']}").json()
        self.assertEqual(product['category'], category['name'])
        self.assertEqual(product['brand'], brand['name'])

    def test_admin_products_pagination_filters_and_public_compatibility(self):
        prefix = 'PAGE-' + uuid4().hex
        ids = []
        for number in range(3):
            response = api('catalog-service', 8002, '/api/products', 'POST', {'category_id': 1, 'brand_id': 1, 'sku': f'{prefix}-{number}', 'name': f'{prefix} product', 'price': 10})
            self.assertEqual(response.status_code, 201, response.text)
            ids.append(response.json()['id'])
        api('catalog-service', 8002, f'/api/products/{ids[-1]}', 'DELETE')

        def page(**filters):
            response = api('catalog-service', 8002, '/api/admin/products?' + urlencode({'q': prefix, 'limit': 2, **filters}))
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()
        first, second = (page(page=1), page(page=2))
        self.assertEqual((first['total'], first['page'], first['limit']), (3, 1, 2))
        self.assertEqual([item['id'] for item in first['items'] + second['items']], list(reversed(ids)))
        self.assertEqual(page(status='active')['total'], 2)
        inactive = page(status='inactive')
        self.assertEqual(inactive['total'], 1)
        self.assertFalse(inactive['items'][0]['is_active'])
        self.assertEqual(page(category_id=1, brand_id=1)['total'], 3)
        self.assertEqual(page(category_id=2147483647)['total'], 0)
        empty = page(page=100)
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['total'], 3)
        self.assertEqual(page(q="' OR 1=1 --")['total'], 0)
        public_ids = {item['id'] for item in api('catalog-service', 8002, '/api/products').json()}
        self.assertNotIn(ids[-1], public_ids)
        self.assertIn(ids[0], public_ids)
        restored = api('catalog-service', 8002, f'/api/products/{ids[-1]}', 'PUT', {'is_active': True})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(page(status='inactive')['total'], 0)

    def test_catalog_partial_update_preserves_fields(self):
        product_id = self.product({'socket': 'AM5'})
        before = api('catalog-service', 8002, f'/api/products/{product_id}').json()
        result = api('catalog-service', 8002, f'/api/products/{product_id}', 'PUT', {'price': 120})
        self.assertEqual(result.status_code, 200, result.text)
        after = api('catalog-service', 8002, f'/api/products/{product_id}').json()
        self.assertEqual(after['name'], before['name'])
        self.assertEqual(after['specs'], before['specs'])
        self.assertEqual(float(after['price']), 120)

    def test_catalog_invalid_requests(self):
        for path, method, body in [('/api/products/1abc', 'GET', None), ('/api/products/1', 'PUT', {}), ('/api/products/1', 'PUT', {'price': -1})]:
            self.assertEqual(api('catalog-service', 8002, path, method, body).status_code, 400)
        self.assertEqual(api('catalog-service', 8002, '/api/products/99999999', 'DELETE').status_code, 404)

    def test_compatibility_invalid_inputs(self):
        for data in ({}, {'components': []}, {'components': [None]}, {'components': [{'type': 'invalid', 'product_id': 1}]}):
            result = api('compatibility-service', 8004, '/api/compatibility/check', 'POST', data)
            self.assertEqual(result.status_code, 400, result.text)

    def test_compatibility_specs_and_catalog_errors(self):
        cpu = self.product({'socket': 'AM5'})
        board = self.product({'socket': 'AM5'})
        body = {'components': [{'type': 'cpu', 'product_id': cpu}, {'type': 'motherboard', 'product_id': board}]}
        result = api('compatibility-service', 8004, '/api/compatibility/check', 'POST', body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['compatible'])
        api('catalog-service', 8002, f'/api/products/{board}', 'PUT', {'specs': {'socket': 'LGA1700'}})
        self.assertFalse(api('compatibility-service', 8004, '/api/compatibility/check', 'POST', body).json()['compatible'])
        api('catalog-service', 8002, f'/api/products/{board}', 'PUT', {'specs': {}})
        self.assertEqual(api('compatibility-service', 8004, '/api/compatibility/check', 'POST', body).status_code, 400)
        body['components'][1]['product_id'] = 99999999
        self.assertEqual(api('compatibility-service', 8004, '/api/compatibility/check', 'POST', body).status_code, 400)
