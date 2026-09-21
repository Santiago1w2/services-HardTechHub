import os
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from contextlib import asynccontextmanager

from uuid import uuid4
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from pymongo.errors import PyMongoError, DuplicateKeyError, CollectionInvalid, OperationFailure
from starlette.concurrency import run_in_threadpool
import jwt
import bcrypt
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator


_mongo_client: MongoClient[dict[str, Any]] | None = None


def get_mongo_client() -> MongoClient[dict[str, Any]]:
    if _mongo_client is None:
        raise HTTPException(status_code=503, detail="MongoDB unavailable")
    return _mongo_client


def get_database() -> Database[dict[str, Any]]:
    return get_mongo_client()[os.getenv("MONGO_DB", "hardtech_identity")]


def get_users_collection() -> Collection[dict[str, Any]]:
    return get_database()[os.getenv("MONGO_USERS_COLLECTION", "users")]


def initialize_database() -> None:
    get_mongo_client().admin.command("ping")
    database = get_database()
    name = os.getenv("MONGO_USERS_COLLECTION", "users")
    if name not in database.list_collection_names():
        try:
            database.create_collection(name)
        except CollectionInvalid:
            pass  # Another worker may have created it.
        except OperationFailure as exc:
            if exc.code != 48:  # NamespaceExists
                raise
    collection = get_users_collection()
    collection.create_index("user_id", unique=True)
    collection.create_index("email", unique=True)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _mongo_client
    get_jwt_secret()
    get_jwt_expiration_minutes()
    try:
        try:
            _mongo_client = MongoClient(
                os.getenv("MONGO_URI", "mongodb://hardtech:hardtech@mongodb:27017/?authSource=admin"),
                serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000,
            )
            await run_in_threadpool(initialize_database)
        except PyMongoError:
            raise RuntimeError("MongoDB initialization failed") from None
        yield
    finally:
        if _mongo_client is not None:
            _mongo_client.close()
            _mongo_client = None


app = FastAPI(title="Identity Service", version="1.0.0", lifespan=lifespan)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must not exceed 72 UTF-8 bytes")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET")
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 bytes")
    return secret


def get_jwt_expiration_minutes() -> int:
    minutes = int(os.getenv("JWT_EXP_MINUTES"))
    if minutes <= 0:
        raise RuntimeError("JWT_EXP_MINUTES must be positive")
    return minutes


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    if len(password.encode("utf-8")) > 72:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        return False


def build_access_token(user_id: str, email: str, roles: list[str]) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=get_jwt_expiration_minutes())
    payload = {
        "sub": user_id,
        "email": email,
        "roles": roles,
        "exp": expires_at,
    }
    return jwt.encode(payload, get_jwt_secret(), algorithm="HS256")


def decode_bearer_token(authorization: str | None) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")

    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=["HS256"],
                             options={"require": ["sub", "exp", "email", "roles"]})
        if not isinstance(payload["sub"], str) or not payload["sub"]:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc


@app.get("/health")
def healthcheck() -> dict[str, str]:
    try:
        get_mongo_client().admin.command("ping")
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable") from None
    return {"service": "identity-service", "status": "healthy", "version": "1.0.0"}


@app.post("/api/auth/register")
def register(payload: RegisterRequest) -> dict[str, str]:
    collection = get_users_collection()
    # Avoid collisions between equal email prefixes in different domains.
    user_id = uuid4().hex
    try:
        if collection.find_one({"email": payload.email}, {"_id": 1}):
            raise HTTPException(status_code=400, detail="User already exists")
        collection.insert_one({
            "user_id": user_id,
            "email": payload.email,
            "password_hash": hash_password(payload.password),
            "roles": ["customer"],
            "preferences": {"currency": "PEN", "theme": "dark"},
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except DuplicateKeyError:
        raise HTTPException(status_code=400, detail="User already exists") from None
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable") from None
    return {"message": "User registered", "user_id": user_id}


@app.post("/api/auth/login")
def login(payload: LoginRequest) -> dict[str, str]:
    try:
        user = get_users_collection().find_one({"email": payload.email})
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable") from None
    if not user or not verify_password(payload.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "access_token": build_access_token(user["user_id"], user["email"], user.get("roles", [])),
        "token_type": "bearer",
    }


@app.get("/api/auth/me")
def get_authenticated_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    payload = decode_bearer_token(authorization)
    try:
        user = get_users_collection().find_one(
            {"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0},
        )
    except PyMongoError:
        raise HTTPException(status_code=503, detail="MongoDB unavailable") from None
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": user["user_id"],
        "email": user["email"],
        "roles": user.get("roles", []),
        "preferences": user.get("preferences", {}),
        "created_at": user.get("created_at"),
    }
