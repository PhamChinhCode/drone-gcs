"""JWT + phân quyền hai cấp operator/admin (mục 10.2)."""
from __future__ import annotations

import time

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


def issue_token(user: dict, secret: str, ttl_min: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user["id"]), "username": user["username"], "role": user["role"],
                       "iat": now, "exp": now + ttl_min * 60}, secret, algorithm="HS256")


def decode_token(token: str, secret: str) -> dict:
    try:
        p = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "token không hợp lệ hoặc đã hết hạn")
    return {"id": int(p["sub"]), "username": p["username"], "role": p["role"]}


def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    if cred is None:
        raise HTTPException(401, "cần đăng nhập")
    return decode_token(cred.credentials, request.app.state.rt.settings.jwt_secret)


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "cần quyền admin")
    return user
