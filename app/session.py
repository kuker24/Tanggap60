from __future__ import annotations

import logging
import secrets
from typing import Any

from fastapi import Request, Response
from itsdangerous import BadSignature, URLSafeSerializer

from app.config import Settings

COOKIE = "t60_sid"
LOGGER = logging.getLogger("tanggap60.session")


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


def is_request_secure(request: Request | None) -> bool:
    if request is None:
        return False
    if request.url.scheme == "https":
        return True
    proto = request.headers.get("x-forwarded-proto", "").lower()
    if proto == "https":
        return True
    cf_visitor = request.headers.get("cf-visitor", "")
    if '"scheme":"https"' in cf_visitor.lower():
        return True
    return False


def set_session_cookie(
    response: Response,
    settings: Settings,
    session_id: str,
    request: Request | None = None,
) -> None:
    if request is not None:
        if is_request_secure(request):
            cookie_secure = True
        elif settings.app_env in {"competition", "production"}:
            cookie_secure = False
            path = str(request.url.path)
            LOGGER.warning(
                "insecure_session_cookie: request to %s over non-TLS HTTP in %s environment; session cookie set with secure=False",
                path,
                settings.app_env,
            )
        else:
            cookie_secure = False
    else:
        cookie_secure = settings.app_env in {"competition", "production"}

    response.set_cookie(
        COOKIE,
        serializer(settings).dumps(session_id),
        httponly=True,
        samesite="lax",
        secure=cookie_secure,
        max_age=60 * 60 * 24,
    )


def error_body(code: str, message: str, recoverable: bool, request_id: str) -> dict[str, Any]:
    return {"code": code, "message": message, "recoverable": recoverable, "request_id": request_id}
