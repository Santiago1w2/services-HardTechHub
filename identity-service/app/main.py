import os
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
import jwt
import bcrypt
from botocore.exceptions import ClientError
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr


def get_dynamodb_resource() -> Any:
    return boto3.resource(
        "dynamodb",
        endpoint_url=os.getenv("DYNAMODB_ENDPOINT", "http://localstack:4566"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
    )


app = FastAPI(title="Identity Service", version="1.0.0")


def get_users_table() -> Any:
    return get_dynamodb_resource().Table(os.getenv("USERS_TABLE", "users"))


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def get_jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "hardtech-dev-secret")


def get_jwt_expiration_minutes() -> int:
    return int(os.getenv("JWT_EXP_MINUTES", "60"))


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def build_access_token(user_id: str, email: str, roles: list[str]) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=get_jwt_expiration_minutes())
    payload = {
        "sub": user_id,
        "email": email,
        "roles": roles,
        "exp": expires_at,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def normalize_roles(raw_roles: Any) -> list[str]:
    if not raw_roles:
        return []
    if isinstance(raw_roles[0], dict):
        return [role.get("S", "") for role in raw_roles if role.get("S")]
    return [role for role in raw_roles if isinstance(role, str)]


def decode_bearer_token(authorization: str | None) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    try:
        return jwt.decode(token, get_jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"service": "identity-service", "status": "healthy", "version": "1.0.0"}


@app.post("/api/auth/register")
def register(payload: RegisterRequest) -> dict[str, str]:
    table = get_users_table()
    user_id = payload.email.split("@")[0]
    item = {
        "user_id": user_id,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "roles": ["customer"],
        "preferences": {"currency": "PEN", "theme": "dark"},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(user_id)")
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            raise HTTPException(status_code=400, detail="User already exists") from exc
        raise HTTPException(status_code=500, detail="DynamoDB error") from exc

    return {"message": "User registered", "user_id": user_id}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, str]:
    table = get_users_table()
    try:
        response = table.scan()
    except ClientError as exc:
        raise HTTPException(status_code=500, detail="DynamoDB error") from exc

    items = response.get("Items", [])
    matched_user = next((item for item in items if item.get("email") == payload.email), None)
    if not matched_user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    stored_hash = matched_user.get("password_hash", "")
    if not verify_password(payload.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    roles = normalize_roles(matched_user.get("roles", []))

    return {
        "access_token": build_access_token(matched_user["user_id"], matched_user["email"], roles),
        "token_type": "bearer",
    }


@app.get("/api/auth/me")
def get_authenticated_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    payload = decode_bearer_token(authorization)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    table = get_users_table()
    try:
        response = table.get_item(Key={"user_id": user_id})
    except ClientError as exc:
        raise HTTPException(status_code=500, detail="DynamoDB error") from exc

    item = response.get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "user_id": item.get("user_id"),
        "email": item.get("email"),
        "roles": normalize_roles(item.get("roles", [])),
        "preferences": item.get("preferences", {}),
        "created_at": item.get("created_at"),
    }
