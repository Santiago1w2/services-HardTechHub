import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import jwt
from fastapi.testclient import TestClient
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

ROOT = Path(__file__).resolve().parents[1]
SECRET = os.environ["JWT_SECRET"]


def load_service(name):
    package = name.replace("-", "_")
    spec = importlib.util.spec_from_file_location(
        package, ROOT / name / "app/main.py",
        submodule_search_locations=[str(ROOT / name / "app")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package] = module
    spec.loader.exec_module(module)
    return module


identity = load_service("identity-service")
analytics = load_service("analytics-service")
orders = load_service("order-service")


def api(service, port, path, method="GET", data=None, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    return httpx.request(method, f"http://{service}:{port}{path}", json=data,
                         headers=headers, timeout=15)


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for service, port, path in [
            ("identity-service", 8001, "/health"), ("catalog-service", 8002, "/health"),
            ("order-service", 8003, "/health"), ("analytics-service", 8005, "/health"),
            ("compatibility-service", 8004, "/api/compatibility/check"),
        ]:
            for attempt in range(60):
                try:
                    api(service, port, path)
                    break
                except httpx.TransportError:
                    time.sleep(1)
            else:
                raise RuntimeError(f"{service} did not start")
        cls.mongo = MongoClient(os.environ["MONGO_URI"], serverSelectionTimeoutMS=5000)
        cls.users = cls.mongo.hardtech_identity.users
        cls.suffix = uuid4().hex
        cls.email = f"user{cls.suffix}@example.com"
        cls.password = "TestPassword123"
        result = api("identity-service", 8001, "/api/auth/register", "POST",
                     {"email": cls.email, "password": cls.password})
        assert result.status_code == 200, result.text
        cls.user_id = result.json()["user_id"]
        cls.token = cls.login(cls.email)
        admin_email = f"admin{cls.suffix}@example.com"
        result = api("identity-service", 8001, "/api/auth/register", "POST",
                     {"email": admin_email, "password": cls.password})
        assert result.status_code == 200, result.text
        cls.users.update_one({"email": admin_email}, {"$set": {"roles": ["admin"]}})
        cls.admin = cls.login(admin_email)

    @classmethod
    def tearDownClass(cls):
        cls.mongo.close()

    @classmethod
    def login(cls, email):
        response = api("identity-service", 8001, "/api/auth/login", "POST",
                       {"email": email, "password": cls.password})
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    def product(self, specs=None):
        result = api("catalog-service", 8002, "/api/products", "POST", {
            "category_id": 1, "brand_id": 1, "sku": "TEST-" + uuid4().hex,
            "name": "Test component", "price": 100, "specs": specs or {},
        }, self.admin)
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["id"]

    def test_register_login_and_me(self):
        user = self.users.find_one({"email": self.email})
        self.assertNotEqual(user["password_hash"], self.password)
        self.assertTrue(identity.verify_password(self.password, user["password_hash"]))
        result = api("identity-service", 8001, "/api/auth/me", token=self.token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result.json()), {"user_id", "email", "roles", "preferences", "created_at"})
        payload = jwt.decode(self.token, SECRET, algorithms=["HS256"])
        self.assertEqual(payload["sub"], self.user_id)
        self.assertEqual(payload["roles"], ["customer"])

    def test_duplicate_registration(self):
        result = api("identity-service", 8001, "/api/auth/register", "POST",
                     {"email": self.email, "password": self.password})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.json()["detail"], "User already exists")

    def test_parallel_duplicate_registration(self):
        data = {"email": f"race{uuid4().hex}@example.com", "password": self.password}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: api("identity-service", 8001, "/api/auth/register", "POST", data), range(2)))
        self.assertEqual(sorted(r.status_code for r in results), [200, 400])

    def test_same_prefix_different_domains(self):
        prefix = uuid4().hex
        ids = []
        for domain in ("example.com", "example.org"):
            result = api("identity-service", 8001, "/api/auth/register", "POST",
                         {"email": f"{prefix}@{domain}", "password": self.password})
            self.assertEqual(result.status_code, 200)
            ids.append(result.json()["user_id"])
        self.assertNotEqual(*ids)

    def test_wrong_password(self):
        result = api("identity-service", 8001, "/api/auth/login", "POST",
                     {"email": self.email, "password": "incorrect"})
        self.assertEqual(result.status_code, 401)

    def test_password_limits(self):
        for password in ("short", "a" * 73, "\u00e9" * 40):
            result = api("identity-service", 8001, "/api/auth/register", "POST",
                         {"email": f"{uuid4().hex}@example.com", "password": password})
            self.assertEqual(result.status_code, 422)

    def test_missing_expired_tampered_tokens(self):
        payload = {"sub": self.user_id, "email": self.email, "roles": ["customer"],
                   "exp": datetime.now(timezone.utc) - timedelta(seconds=30)}
        expired = jwt.encode(payload, SECRET, algorithm="HS256")
        payload["exp"] = datetime.now(timezone.utc) + timedelta(minutes=10)
        tampered = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        missing_exp = jwt.encode({"sub": self.user_id, "email": self.email, "roles": []}, SECRET, algorithm="HS256")
        for token in (None, expired, tampered, missing_exp):
            self.assertEqual(api("identity-service", 8001, "/api/auth/me", token=token).status_code, 401)
            self.assertEqual(api("order-service", 8003, "/api/orders", token=token).status_code, 401)

    def test_catalog_permissions(self):
        product_id = self.product()
        for token, status in ((None, 401), (self.token, 403)):
            result = api("catalog-service", 8002, f"/api/products/{product_id}", "PUT", {"price": 9}, token)
            self.assertEqual(result.status_code, status, result.text)
            result = api("catalog-service", 8002, f"/api/products/{product_id}", "DELETE", token=token)
            self.assertEqual(result.status_code, status)
        self.assertEqual(api("catalog-service", 8002, "/api/products").status_code, 200)

    def test_catalog_partial_update_preserves_fields(self):
        product_id = self.product({"socket": "AM5"})
        before = api("catalog-service", 8002, f"/api/products/{product_id}").json()
        result = api("catalog-service", 8002, f"/api/products/{product_id}", "PUT", {"price": 120}, self.admin)
        self.assertEqual(result.status_code, 200, result.text)
        after = api("catalog-service", 8002, f"/api/products/{product_id}").json()
        self.assertEqual(after["name"], before["name"])
        self.assertEqual(after["specs"], before["specs"])
        self.assertEqual(float(after["price"]), 120)

    def test_catalog_invalid_requests(self):
        for path, method, body in [
            ("/api/products/1abc", "GET", None),
            ("/api/products/1", "PUT", {}),
            ("/api/products/1", "PUT", {"price": -1}),
        ]:
            self.assertEqual(api("catalog-service", 8002, path, method, body, self.admin).status_code, 400)
        self.assertEqual(api("catalog-service", 8002, "/api/products/99999999", "DELETE", token=self.admin).status_code, 404)

    def test_order_owner_and_admin(self):
        product_id = self.product()
        body = {"user_id": self.user_id, "items": [{"product_id": product_id, "quantity": 2}]}
        result = api("order-service", 8003, "/api/orders", "POST", body, self.token)
        self.assertEqual(result.status_code, 201, result.text)
        order_id = result.json()["order_id"]
        self.assertEqual(float(result.json()["total_amount"]), 261)
        self.assertEqual(api("order-service", 8003, f"/api/orders/{order_id}", token=self.token).status_code, 200)
        stranger = jwt.encode({"sub": "absent", "email": "x@example.com", "roles": [],
                              "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, SECRET, algorithm="HS256")
        self.assertEqual(api("order-service", 8003, f"/api/orders/{order_id}", token=stranger).status_code, 401)
        self.assertEqual(api("order-service", 8003, f"/api/orders/{order_id}/status", "PATCH",
                             {"status": "PAID"}, self.token).status_code, 403)
        self.assertEqual(api("order-service", 8003, f"/api/orders/{order_id}/status", "PATCH",
                             {"status": "PAID"}, self.admin).status_code, 200)
        other = api("identity-service", 8001, "/api/auth/register", "POST",
                    {"email": f"other{uuid4().hex}@example.com", "password": self.password})
        other_token = self.login(self.users.find_one({"user_id": other.json()["user_id"]})["email"])
        self.assertEqual(api("order-service", 8003, f"/api/orders/{order_id}", token=other_token).status_code, 403)
        self.assertEqual(api("order-service", 8003, f"/api/orders/user/{self.user_id}", token=other_token).status_code, 403)

    def test_order_impersonation_quantity_and_inactive_product(self):
        product_id = self.product()
        body = {"user_id": "someone-else", "items": [{"product_id": product_id, "quantity": 1}]}
        self.assertEqual(api("order-service", 8003, "/api/orders", "POST", body, self.token).status_code, 403)
        body["user_id"] = self.user_id
        for quantity in (0, -1):
            body["items"][0]["quantity"] = quantity
            self.assertEqual(api("order-service", 8003, "/api/orders", "POST", body, self.token).status_code, 422)
        body["items"][0]["quantity"] = 1
        api("catalog-service", 8002, f"/api/products/{product_id}", "DELETE", token=self.admin)
        self.assertEqual(api("order-service", 8003, "/api/orders", "POST", body, self.token).status_code, 400)

    def test_analytics_permissions(self):
        for token, status in ((None, 401), (self.token, 403)):
            for path in ("/api/analytics/events/count", "/api/analytics/top-products"):
                self.assertEqual(api("analytics-service", 8005, path, token=token).status_code, status)

    def test_compatibility_invalid_inputs(self):
        for data in ({}, {"components": []}, {"components": [None]},
                     {"components": [{"type": "invalid", "product_id": 1}]}):
            result = api("compatibility-service", 8004, "/api/compatibility/check", "POST", data)
            self.assertEqual(result.status_code, 400, result.text)

    def test_compatibility_specs_and_catalog_errors(self):
        cpu = self.product({"socket": "AM5"})
        board = self.product({"socket": "AM5"})
        body = {"components": [{"type": "cpu", "product_id": cpu}, {"type": "motherboard", "product_id": board}]}
        result = api("compatibility-service", 8004, "/api/compatibility/check", "POST", body)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["compatible"])
        api("catalog-service", 8002, f"/api/products/{board}", "PUT", {"specs": {"socket": "LGA1700"}}, self.admin)
        self.assertFalse(api("compatibility-service", 8004, "/api/compatibility/check", "POST", body).json()["compatible"])
        api("catalog-service", 8002, f"/api/products/{board}", "PUT", {"specs": {}}, self.admin)
        self.assertEqual(api("compatibility-service", 8004, "/api/compatibility/check", "POST", body).status_code, 400)
        body["components"][1]["product_id"] = 99999999
        self.assertEqual(api("compatibility-service", 8004, "/api/compatibility/check", "POST", body).status_code, 400)

    def test_indexes_idempotent_lifespan(self):
        for _ in range(2):
            with TestClient(identity.app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertIs(identity.get_mongo_client(), identity.get_mongo_client())
        self.assertIsNone(identity._mongo_client)
        info = self.users.index_information()
        self.assertTrue(info["email_1"]["unique"])
        self.assertTrue(info["user_id_1"]["unique"])
        user = self.users.find_one({"user_id": self.user_id})
        user.pop("_id")
        user["email"] = f"unique{uuid4().hex}@example.com"
        with self.assertRaises(DuplicateKeyError):
            self.users.insert_one(user)


class FailureTests(unittest.TestCase):
    def test_mongo_unavailable_is_sanitized(self):
        broken = MagicMock()
        broken.find_one.side_effect = PyMongoError("sensitive connection details")
        with patch.object(identity, "get_users_collection", return_value=broken):
            with TestClient(identity.app, raise_server_exceptions=False) as client:
                result = client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "password"})
                self.assertEqual(result.status_code, 503)
                self.assertEqual(result.json()["detail"], "MongoDB unavailable")

    def test_failed_startup_closes_client(self):
        fake = MagicMock()
        fake.admin.command.side_effect = PyMongoError("sensitive details")
        with patch.object(identity, "MongoClient", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "^MongoDB initialization failed$"):
                with TestClient(identity.app):
                    pass
        fake.close.assert_called_once()
        self.assertIsNone(identity._mongo_client)

    def test_health_does_not_claim_healthy_after_mongo_failure(self):
        with TestClient(identity.app) as client:
            broken = MagicMock()
            broken.admin.command.side_effect = PyMongoError("secret")
            with patch.object(identity, "get_mongo_client", return_value=broken):
                self.assertEqual(client.get("/health").status_code, 503)

    def test_identity_unavailable_fails_closed(self):
        with patch.dict(os.environ, {"IDENTITY_SERVICE_URL": "http://127.0.0.1:1"}):
            with TestClient(orders.app) as client:
                result = client.get("/api/orders", headers={"Authorization": "Bearer test"})
                self.assertEqual(result.status_code, 503)

    def test_s3_pagination(self):
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "raw/events/1.json"}]},
            {"Contents": [{"Key": "raw/events/2.json"}, {"Key": "other.txt"}]},
        ]
        with patch.object(analytics, "get_s3_client", return_value=s3):
            self.assertEqual(analytics.list_event_objects(), ["raw/events/1.json", "raw/events/2.json"])

    def test_s3_invalid_and_valid_json(self):
        for raw, valid in [(b"{", False), (b"[1]", False),
                           (b'[{"event_type": []}]', False),
                           (b'[{"event_type":"PRODUCT_VIEW","product_id":1}]', True)]:
            body = io.BytesIO(raw)
            s3 = MagicMock()
            s3.get_object.return_value = {"Body": body}
            with patch.object(analytics, "get_s3_client", return_value=s3), patch.object(analytics, "list_event_objects", return_value=["test.json"]):
                if valid:
                    self.assertEqual(len(analytics.load_events()), 1)
                else:
                    with self.assertRaises(Exception) as caught:
                        analytics.load_events()
                    self.assertEqual(caught.exception.status_code, 502)
                self.assertTrue(body.closed)


if __name__ == "__main__":
    unittest.main()
