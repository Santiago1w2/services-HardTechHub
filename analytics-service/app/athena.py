import os
import time
from decimal import Decimal

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException


def get_athena_client():
    return boto3.client("athena", config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 2}))


def configuration():
    values = {key: os.getenv(key, "") for key in
              ("S3_BUCKET", "GLUE_DATABASE", "ATHENA_WORKGROUP", "ATHENA_OUTPUT_LOCATION")}
    if not all(values.values()) or not values["ATHENA_OUTPUT_LOCATION"].startswith("s3://"):
        raise HTTPException(503, "Configure S3_BUCKET, GLUE_DATABASE, ATHENA_WORKGROUP and ATHENA_OUTPUT_LOCATION")
    return values


def query(sql):
    settings = configuration()
    try:
        client = get_athena_client()
        result = client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": settings["GLUE_DATABASE"], "Catalog": "AwsDataCatalog"},
            WorkGroup=settings["ATHENA_WORKGROUP"],
            ResultConfiguration={"OutputLocation": settings["ATHENA_OUTPUT_LOCATION"]})
        query_id = result["QueryExecutionId"]
        deadline = time.monotonic() + float(os.getenv("ATHENA_QUERY_TIMEOUT_SECONDS", "30"))
        while True:
            status = client.get_query_execution(QueryExecutionId=query_id)["QueryExecution"]["Status"]["State"]
            if status == "SUCCEEDED":
                break
            if status in ("FAILED", "CANCELLED"):
                raise HTTPException(502, "Athena query failed")
            if time.monotonic() >= deadline:
                client.stop_query_execution(QueryExecutionId=query_id)
                raise HTTPException(504, "Athena query timed out")
            time.sleep(0.25)
        rows = []
        first = True
        for page in client.get_paginator("get_query_results").paginate(QueryExecutionId=query_id):
            columns = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
            for row in page["ResultSet"]["Rows"]:
                if first:
                    first = False
                    continue
                item = {}
                for column, value in zip(columns, row["Data"]):
                    raw = value.get("VarCharValue")
                    kind = column["Type"]
                    if raw is not None and kind in ("bigint", "integer", "smallint"):
                        raw = int(raw)
                    elif raw is not None and (kind.startswith("decimal") or kind in ("double", "float")):
                        raw = str(Decimal(raw))
                    item[column["Name"]] = raw
                rows.append(item)
        return rows
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "AWS analytics unavailable") from None
