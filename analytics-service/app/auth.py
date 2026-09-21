import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import Depends, Header, HTTPException


def get_current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    url = os.getenv("IDENTITY_SERVICE_URL").rstrip("/")
    request = Request(f"{url}/api/auth/me", headers={"Authorization": authorization})
    try:
        with urlopen(request, timeout=5) as response:
            user = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise HTTPException(status_code=401, detail="Invalid or expired token") from None
        raise HTTPException(status_code=503, detail="Identity Service unavailable") from None
    except (URLError, TimeoutError, OSError):
        raise HTTPException(status_code=503, detail="Identity Service unavailable") from None
    except (ValueError, UnicodeError):
        raise HTTPException(status_code=502, detail="Invalid Identity Service response") from None
    if (not isinstance(user, dict) or not isinstance(user.get("user_id"), str)
            or not user["user_id"] or not isinstance(user.get("roles"), list)
            or not all(isinstance(role, str) for role in user["roles"])):
        raise HTTPException(status_code=502, detail="Invalid Identity Service response")
    return user


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def require_owner_or_admin(user: dict[str, Any], user_id: str) -> None:
    if user["user_id"] != user_id and "admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Access denied")
