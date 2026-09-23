import io
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg2
from fastapi import HTTPException
from fastapi.testclient import TestClient
from botocore.exceptions import ClientError
import test_services
from test_services import api, orders, analytics, athena, pipeline


class BusinessTests(unittest.TestCase):
    setUpClass = classmethod(test_services.IntegrationTests.setUpClass.__func__)
    product = test_services.IntegrationTests.product
    inventory = test_services.IntegrationTests.inventory
    reservation = test_services.IntegrationTests.reservation
    operation = test_services.IntegrationTests.operation

    def test_create_and_get_inventory(self):
        pid = self.product(stock=None)
        result = api("inventory-service",8006,"/inventory","POST",{"product_id":pid,"stock":10,"reorder_point":2})
        self.assertEqual(result.status_code,201,result.text)
        self.assertEqual(self.inventory(pid)["available_stock"],10)
        self.assertEqual(self.inventory(pid)["reorder_point"],2)
        self.assertEqual(api("inventory-service",8006,"/inventory","POST",{"product_id":pid,"stock":1}).status_code,409)

    def test_reserve_release_idempotent(self):
        pid = self.product(stock=10)
        body = self.reservation(pid,3)
        self.operation("reserve",body)
        self.operation("reserve",body)
        self.assertEqual(self.inventory(pid)["reserved_stock"],3)
        self.operation("release",body)
        self.operation("release",body)
        self.assertEqual(self.inventory(pid)["available_stock"],10)
        self.operation("reserve",body,409)

    def test_confirm_sale_idempotent(self):
        pid = self.product(stock=10)
        body = self.reservation(pid,3)
        self.operation("reserve",body)
        self.operation("confirm",body)
        self.operation("confirm",body)
        self.assertEqual(self.inventory(pid)["stock"],7)
        self.assertEqual(self.inventory(pid)["reserved_stock"],0)
        self.operation("release",body,409)

    def test_cannot_oversell_or_make_stock_negative(self):
        pid = self.product(stock=2)
        self.operation("reserve",self.reservation(pid,3),409)
        self.assertEqual(self.inventory(pid)["reserved_stock"],0)
        self.operation("reserve",self.reservation(pid,2))
        self.assertEqual(api("inventory-service",8006,f"/inventory/{pid}","PUT",{"stock":1}).status_code,409)
        for value in (-1,1.5):
            self.assertEqual(api("inventory-service",8006,f"/inventory/{pid}","PUT",{"stock":value}).status_code,422)
        self.assertEqual(self.inventory(pid)["stock"],2)

    def test_atomic_multi_item_reservation(self):
        a,b = self.product(stock=5),self.product(stock=0)
        body=self.reservation(a,2)
        body["items"].append({"product_id":b,"quantity":1})
        self.operation("reserve",body,409)
        self.assertEqual(self.inventory(a)["reserved_stock"],0)

    def test_concurrent_reservations(self):
        pid=self.product(stock=5)
        bodies=[self.reservation(pid,4),self.reservation(pid,4)]
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda b: api("inventory-service",8006,"/inventory/reserve","POST",b),bodies))
        self.assertEqual(sorted(r.status_code for r in results),[200,409])
        self.assertEqual(self.inventory(pid)["reserved_stock"],4)

    def test_duplicate_items_and_different_retry_payload(self):
        pid=self.product(stock=10)
        body=self.reservation(pid,2)
        body["items"].append({"product_id":pid,"quantity":3})
        self.operation("reserve",body)
        self.assertEqual(self.inventory(pid)["reserved_stock"],5)
        body["items"]=[{"product_id":pid,"quantity":1}]
        self.operation("reserve",body,409)
        self.operation("release",body,409)

    def test_release_before_reserve_prevents_late_request(self):
        body=self.reservation(self.product(stock=5),2)
        self.operation("release",body)
        self.operation("reserve",body,409)

    def test_order_create_confirm_ship(self):
        pid=self.product(stock=10)
        body={"items":[{"product_id":pid,"quantity":2}]}
        headers={"Idempotency-Key":uuid4().hex}
        first=api("order-service",8003,"/api/orders","POST",body,headers)
        self.assertEqual(first.status_code,201,first.text)
        oid=first.json()["order_id"]
        again=api("order-service",8003,"/api/orders","POST",body,headers)
        self.assertEqual(again.json()["order_id"],oid)
        self.assertEqual(float(first.json()["total_amount"]),261)
        self.assertEqual(self.inventory(pid)["reserved_stock"],2)
        for status in ("PAID","PAID","SHIPPED"):
            result=api("order-service",8003,f"/api/orders/{oid}/status","PATCH",{"status":status})
            self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.inventory(pid)["stock"],8)
        self.assertEqual(self.inventory(pid)["reserved_stock"],0)
        self.assertEqual(api("order-service",8003,f"/api/orders/{oid}/status","PATCH",{"status":"CANCELLED"}).status_code,409)
        details=api("order-service",8003,f"/api/orders/{oid}").json()
        self.assertEqual(details["order"]["id"],oid)
        body["items"][0]["quantity"]=3
        self.assertEqual(api("order-service",8003,"/api/orders","POST",body,headers).status_code,409)

    def test_order_cancel_and_reject_shortage(self):
        pid=self.product(stock=3)
        result=api("order-service",8003,"/api/orders","POST",{"items":[{"product_id":pid,"quantity":2}]})
        self.assertEqual(result.status_code,201,result.text)
        oid=result.json()["order_id"]
        for _ in range(2):
            result=api("order-service",8003,f"/api/orders/{oid}/status","PATCH",{"status":"CANCELLED"})
            self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.inventory(pid)["available_stock"],3)
        result=api("order-service",8003,"/api/orders","POST",{"items":[{"product_id":pid,"quantity":4}]})
        self.assertEqual(result.status_code,409,result.text)
        rejected=result.json()["detail"]["order_id"]
        self.assertEqual(api("order-service",8003,f"/api/orders/{rejected}").json()["order"]["status"],"CANCELLED")

    def test_order_recovers_lost_reservation_response(self):
        pid=self.product(stock=10)
        original=orders.inventory_operation
        def lost(action,oid,items):
            original(action,oid,items)
            raise HTTPException(503,"Simulated lost response")
        with patch.object(orders,"inventory_operation",side_effect=lost), TestClient(orders.app) as client:
            response=client.post("/api/orders",json={"items":[{"product_id":pid,"quantity":2}]})
            self.assertEqual(response.status_code,503,response.text)
            oid=response.json()["detail"]["order_id"]
        self.assertEqual(self.inventory(pid)["reserved_stock"],2)
        result=api("order-service",8003,f"/api/orders/{oid}/retry","POST")
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.inventory(pid)["reserved_stock"],2)

    def test_order_recovers_lost_confirmation_response(self):
        pid=self.product(stock=5)
        result=api("order-service",8003,"/api/orders","POST",{"items":[{"product_id":pid,"quantity":2}]})
        oid=result.json()["order_id"]
        original=orders.inventory_operation
        def lost(action,oid,items):
            original(action,oid,items)
            raise HTTPException(503,"Simulated lost response")
        with patch.object(orders,"inventory_operation",side_effect=lost), TestClient(orders.app) as client:
            response=client.patch(f"/api/orders/{oid}/status",json={"status":"PAID"})
            self.assertEqual(response.status_code,503,response.text)
        result=api("order-service",8003,f"/api/orders/{oid}/status","PATCH",{"status":"PAID"})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.inventory(pid)["stock"],3)

    def test_combined_order_quantity_rejected_before_persistence(self):
        with TestClient(orders.app) as client, patch.object(orders, "get_connection") as connect:
            response=client.post("/api/orders",json={"items":[
                {"product_id":1,"quantity":2147483647},
                {"product_id":1,"quantity":1}]})
            self.assertEqual(response.status_code,422,response.text)
            connect.assert_not_called()

    def test_order_validation_and_list_filters(self):
        pid=self.product()
        for items in ([],[{"product_id":pid,"quantity":0}],[{"product_id":pid,"quantity":-1}]):
            self.assertEqual(api("order-service",8003,"/api/orders","POST",{"items":items}).status_code,422)
        for query in ("page=0","limit=101","status=UNKNOWN","date_from=2026-02-01&date_to=2026-01-01"):
            self.assertEqual(api("order-service",8003,"/api/admin/orders?"+query).status_code,422)
        api("catalog-service",8002,f"/api/products/{pid}","DELETE")
        self.assertEqual(api("order-service",8003,"/api/orders","POST",{"items":[{"product_id":pid,"quantity":1}]}).status_code,400)

    def test_catalog_persistence_and_indexes(self):
        pid=self.product({"socket":"AM5"})
        conn=psycopg2.connect(host="postgres",dbname="hardtech_catalog",user="catalog_app",
            password=os.environ["CATALOG_POSTGRES_PASSWORD"])
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT specs FROM products WHERE id=%s",(pid,))
                specs=cursor.fetchone()[0]
                self.assertEqual(specs["socket"],"AM5")
                cursor.execute("SELECT indexname FROM pg_indexes WHERE tablename='products'")
                indexes=[row[0] for row in cursor.fetchall()]
        finally:
            conn.close()
        self.assertTrue(any(name.endswith("_sku_key") for name in indexes))
        result=api("catalog-service",8002,f"/api/products/{pid}","PUT",{"specifications":{"cores":8}})
        self.assertEqual(result.status_code,200,result.text)
        detail=api("catalog-service",8002,f"/api/products/{pid}").json()
        self.assertEqual(detail["specifications"],{"cores":8})
        self.assertEqual(detail["specs"],detail["specifications"])

    def test_postgres_database_separation(self):
        conn=psycopg2.connect(host="postgres",dbname="hardtech_catalog",user="catalog_app",
            password=os.environ["CATALOG_POSTGRES_PASSWORD"],connect_timeout=5)
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT datname FROM pg_database WHERE datistemplate=false ORDER BY datname")
                databases=[row[0] for row in cursor.fetchall()]
        finally:
            conn.close()
        self.assertIn("hardtech_catalog",databases)
        self.assertNotIn("hardtech_inventory",databases)
        self.assertNotIn("hardtech_compatibility",databases)
        for db in ("hardtech_inventory","hardtech_compatibility"):
            with self.assertRaises(psycopg2.OperationalError):
                psycopg2.connect(host="postgres",dbname=db,user="catalog_app",
                    password=os.environ["CATALOG_POSTGRES_PASSWORD"],connect_timeout=5)

    def test_analytics_export_real_apis_without_duplicate_files(self):
        pid=self.product(stock=5)
        result=api("order-service",8003,"/api/orders","POST",{"items":[{"product_id":pid,"quantity":1}]})
        self.assertEqual(result.status_code,201,result.text)
        s3=MagicMock()
        for _ in range(2):
            result=pipeline.refresh(s3,"test-bucket",[])
            self.assertGreater(result["records"],0)
        calls=s3.put_object.call_args_list
        self.assertEqual(calls[0].kwargs["Key"],calls[1].kwargs["Key"])
        rows=[json.loads(line) for line in calls[-1].kwargs["Body"].splitlines()]
        self.assertTrue(any(x["event_type"]=="ORDER" for x in rows))
        self.assertTrue(any(x["event_type"]=="INVENTORY_MOVEMENT" for x in rows))
        keys=[(x["event_type"],x.get("order_id"),x.get("product_id")) for x in rows if x["event_type"]=="ORDER"]
        self.assertEqual(len(keys),len(set(keys)))


    def test_orders_pagination_and_dates(self):
        # Keep fixture dates inside the MySQL TIMESTAMP range.
        conn=orders.get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.executemany("INSERT INTO orders(status,created_at) VALUES (%s,%s)",
                    [("PENDING","2031-01-15 23:59:59")]*54+[("PAID","2031-01-16 00:00:00")])
            conn.commit()
            base="/api/admin/orders?date_from=2031-01-15&date_to=2031-01-16&limit=50"
            first=api("order-service",8003,base+"&page=1").json()
            second=api("order-service",8003,base+"&page=2").json()
            self.assertEqual(first["total"],55)
            self.assertEqual(len(first["items"]),50)
            self.assertEqual(len(second["items"]),5)
            ids=[x["id"] for x in first["items"]+second["items"]]
            self.assertEqual(len(set(ids)),55)
            self.assertEqual(api("order-service",8003,base+"&status=PAID").json()["total"],1)
        finally:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM orders WHERE created_at BETWEEN '2031-01-15' AND '2031-01-17'")
            conn.commit()
            conn.close()

    def test_inventory_movement_ledger(self):
        pid=self.product(stock=8)
        body=self.reservation(pid,2)
        self.operation("reserve",body)
        self.operation("confirm",body)
        self.operation("confirm",body)
        rows=api("inventory-service",8006,"/inventory/movements?limit=1000").json()
        rows=[x for x in rows if x["product_id"]==pid]
        self.assertEqual([x["movement_type"] for x in rows],["STOCK_IN","RESERVE","SALE"])
        self.assertEqual([x["quantity"] for x in rows],[8,2,2])
        self.assertIsNone(rows[0]["order_id"])
        self.assertEqual(rows[-1]["order_id"],body["order_id"])

def test_compatibility_stateless(self):
        a, b = self.product({"socket": "AM5"}), self.product({"socket": "AM5"})
        result = api("compatibility-service", 8004, "/api/compatibility/check", "POST",
            {"components": [{"type": "cpu", "product_id": a}, {"type": "motherboard", "product_id": b}]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["compatible"])
        with self.assertRaises(psycopg2.OperationalError):
            psycopg2.connect(host="postgres", dbname="hardtech_compatibility", user="catalog_app",
                password=os.environ["CATALOG_POSTGRES_PASSWORD"], connect_timeout=5)


class AnalyticsTests(unittest.TestCase):
    def test_s3_pagination(self):
        s3=MagicMock()
        s3.get_paginator.return_value.paginate.return_value=[
            {"Contents":[{"Key":"raw/events/1.json"}]},
            {"Contents":[{"Key":"raw/events/2.json"},{"Key":"other.txt"}]}]
        with patch.object(analytics,"get_s3_client",return_value=s3):
            self.assertEqual(analytics.list_event_objects(),["raw/events/1.json","raw/events/2.json"])

    def test_s3_data_validation(self):
        for raw,valid in [(b"{",False),(b"[1]",False),(b'[{"event_type":[]}]',False),
                          (b'[{"event_type":"PRODUCT_VIEW","product_id":1}]',True)]:
            body=io.BytesIO(raw)
            s3=MagicMock()
            s3.get_object.return_value={"Body":body}
            with patch.object(analytics,"get_s3_client",return_value=s3),patch.object(analytics,"list_event_objects",return_value=["test.json"]):
                if valid:
                    self.assertEqual(len(analytics.load_events()),1)
                else:
                    with self.assertRaises(HTTPException):
                        analytics.load_events()
                self.assertTrue(body.closed)

    def fake_athena(self,status="SUCCEEDED"):
        client=MagicMock()
        client.start_query_execution.return_value={"QueryExecutionId":"q"}
        client.get_query_execution.return_value={"QueryExecution":{"Status":{"State":status}}}
        return client

    def test_athena_pagination_and_types(self):
        client=self.fake_athena()
        meta={"ColumnInfo":[{"Name":"units","Type":"bigint"},{"Name":"revenue","Type":"decimal(16,2)"}]}
        client.get_paginator.return_value.paginate.return_value=[
            {"ResultSet":{"ResultSetMetadata":meta,"Rows":[{"Data":[{"VarCharValue":"units"},{"VarCharValue":"revenue"}]},
                {"Data":[{"VarCharValue":"2"},{"VarCharValue":"12.50"}]}]}},
            {"ResultSet":{"ResultSetMetadata":meta,"Rows":[{"Data":[{"VarCharValue":"3"},{}]}]}}]
        with patch.object(athena,"get_athena_client",return_value=client):
            self.assertEqual(athena.query("SELECT 1"),[{"units":2,"revenue":"12.50"},{"units":3,"revenue":None}])

    def test_athena_failure_and_timeout(self):
        for status,code in [("FAILED",502),("CANCELLED",502),("RUNNING",504)]:
            client=self.fake_athena(status)
            with patch.object(athena,"get_athena_client",return_value=client),patch.dict(os.environ,{"ATHENA_QUERY_TIMEOUT_SECONDS":"0"}):
                with self.assertRaises(HTTPException) as caught:
                    athena.query("SELECT 1")
                self.assertEqual(caught.exception.status_code,code)
                if code==504:
                    client.stop_query_execution.assert_called_once_with(QueryExecutionId="q")

    def test_aws_failure_is_sanitized(self):
        client=self.fake_athena()
        client.start_query_execution.side_effect=ClientError({"Error":{"Code":"AccessDenied","Message":"private"}},"StartQueryExecution")
        with patch.object(athena,"get_athena_client",return_value=client):
            with self.assertRaises(HTTPException) as caught:
                athena.query("SELECT 1")
            self.assertEqual(caught.exception.status_code,503)
            self.assertNotIn("private",str(caught.exception.detail))

    def test_missing_aws_configuration(self):
        with patch.dict(os.environ,{"S3_BUCKET":""}):
            with self.assertRaises(HTTPException) as caught:
                athena.configuration()
            self.assertEqual(caught.exception.status_code,503)
