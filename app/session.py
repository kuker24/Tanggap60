from __future__ import annotations

import secrets
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import Settings

COOKIE = "t60_sid"


def serializer(settings: Settings) -> URLSafeSerializer:
    return URLSafeSerializer(settings.secret_key, salt="tanggap60-session")


def get_session_id(request: Request, settings: Settings) -> str:
    raw = request.cookies.get(COOKIE)
    if not raw:
        return secrets.token_hex(16)
    try:
        value = serializer(settings).loads(raw)
        if isinstance(value, str) and value:
            return value
    except BadSignature:
        return secrets.token_hex(16)
    return secrets.token_hex(16)


def set_session_cookie(response: Response, settings: Settings, session_id: str) -> None:
    response.set_cookie(
        COOKIE,
        serializer(settings).dumps(session_id),
        httponly=True,
        samesite="lax",
        secure=settings.app_env in {"competition", "production"},
        max_age=60 * 60 * 24,
    )


def error_body(code: str, message: str, recoverable: bool, request_id: str) -> dict[str, Any]:
    return {"code": code, "message": message, "recoverable": recoverable, "request_id": request_id}
