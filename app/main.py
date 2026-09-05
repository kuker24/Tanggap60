from __future__ import annotations

import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.agent_router import agent_api
from app.api.router import api
from app.deps import AppContainer, build_container
from app.domain.errors import AppError
from app.infrastructure.db import CaseRow
from app.infrastructure.jobs import JobQueue
from app.infrastructure.logging import configure_logging
from app.infrastructure.resources import available_ram_mb, cpu_percent, free_disk_mb, process_rss_mb
from app.session import error_body, get_session_id, set_session_cookie
from app.web.routes import TEMPLATES, _friendly_file_error, web

LOGGER = configure_logging("tanggap60-web")
STATIC_DIR = Path(__file__).parent / "web" / "static"

_CASE_RE = re.compile(r"/cases/([A-Za-z0-9-]+)/")


def _web_error_context(request: Request, exc: AppError, request_id: str) -> dict | None:
    accept = request.headers.get("accept", "")
    if "text/html" not in accept or str(request.url.path).startswith("/api/"):
        return None
    path = str(request.url.path)
    case_id = (_CASE_RE.search(path) or [None, None])[1]
    if exc.code == "CASE_EXPIRED":
        return {
            "title": "Kasus ini sudah berakhir",
            "message": "Kasus tidak bisa dibuka lagi setelah 60 menit. Mulai kasus baru jika masih perlu bantuan.",
            "cta_url": "/",
            "cta_label": "Mulai kasus baru",
            "secondary_url": None,
            "secondary_label": "",
            "detail": f"Kode bantuan: {request_id}",
        }
    if exc.code in {"NOT_FOUND", "FORBIDDEN"}:
        return {
            "title": "Kasus tidak bisa dibuka",
            "message": "Kasus mungkin sudah berakhir, sudah dihapus, atau dibuat di perangkat lain.",
            "cta_url": "/",
            "cta_label": "Mulai kasus baru",
            "secondary_url": None,
            "secondary_label": "",
            "detail": f"Kode bantuan: {request_id}",
        }
    if exc.code in {"INVALID_FILE_TYPE", "UPLOAD_LIMIT_EXCEEDED", "EVIDENCE_PARSE_FAILED"}:
        ctx = {
            "title": "File-nya bermasalah",
            "message": _friendly_file_error(exc.message),
            "cta_url": f"/cases/{case_id}/intake" if case_id else "/",
            "cta_label": "Kembali tambah bukti" if case_id else "Mulai dari beranda",
            "secondary_url": "/",
            "secondary_label": "Ke beranda",
        }
        return ctx
    return {
        "title": "Ada yang macet",
        "message": "Coba muat ulang halaman ini. Kalau masih macet, mulai dari beranda.",
        "cta_url": path,
        "cta_label": "Muat ulang",
        "secondary_url": "/",
        "secondary_label": "Ke beranda",
    }


def create_app(container: AppContainer | None = None) -> FastAPI:
    app = FastAPI(title="SatuAman Tanggap60", version="2.0.0")
    app.state.container = container or build_container()
    app.include_router(api)
    app.include_router(agent_api)
    app.include_router(web)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def session_and_db(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-Id") or uuid4().hex[:12]
        request.state.request_id = request_id
        request.state.session_id = get_session_id(request, app.state.container.settings)
        db = app.state.container.sessions()
        request.state.db = db
        try:
            response = await call_next(request)
            db.commit()
        except AppError as exc:
            db.rollback()
            page = _web_error_context(request, exc, request_id)
            if page is None:
                response = JSONResponse(
                    status_code=exc.http_status,
                    content=error_body(exc.code, exc.message, exc.recoverable, request_id),
                )
            else:
                response = TEMPLATES.TemplateResponse(
                    request,
                    "error.html",
                    context={"request_id": request_id, **page},
                    status_code=exc.http_status,
                )
        except Exception:
            db.rollback()
            LOGGER.exception("unhandled", extra={"request_id": request_id})
            accept = request.headers.get("accept", "")
            path = str(request.url.path)
            case_id = (_CASE_RE.search(path) or [None, None])[1]
            if "text/html" in accept and not path.startswith("/api/"):
                response = TEMPLATES.TemplateResponse(
                    request,
                    "error.html",
                    context={
                        "request_id": request_id,
                        "title": "Sedang ada gangguan",
                        "message": "Halaman belum bisa dibuka. Data yang sudah tersimpan tidak dikirim ke bank atau polisi.",
                        "cta_url": path,
                        "cta_label": "Coba lagi" if case_id else "Ke beranda",
                        "secondary_url": "/" if case_id else None,
                        "secondary_label": "Ke beranda" if case_id else "",
                        "detail": f"Kode bantuan: {request_id}",
                    },
                    status_code=500,
                )
            else:
                response = JSONResponse(
                    status_code=500,
                    content=error_body("INTERNAL", "terjadi kesalahan", False, request_id),
                )
        finally:
            db.close()
        set_session_cookie(response, app.state.container.settings, request.state.session_id)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        path = str(request.url.path)
        if path.startswith("/api/") or path.startswith("/cases/") or path == "/demo/metrics":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready() -> JSONResponse:
        session = app.state.container.sessions()
        try:
            session.query(CaseRow).limit(1).all()
            if not app.state.container.storage.root.exists():
                raise OperationalError("storage", {}, Exception("missing"))
        except Exception as exc:
            session.close()
            return JSONResponse(
                status_code=503, content={"status": "not_ready", "error": str(type(exc).__name__)}
            )
        session.close()
        ocr_ok = shutil.which("tesseract") is not None
        env = app.state.container.settings.app_env
        components = {"db": True, "storage": True, "ocr": ocr_ok}
        if env in {"competition", "production"} and not ocr_ok:
            return JSONResponse(status_code=503, content={"status": "not_ready", "components": components})
        return JSONResponse({"status": "ready", "components": components})

    @app.get("/demo/metrics", response_model=None)
    def metrics(request: Request):
        if app.state.container.settings.app_env not in {"development", "test"}:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        session = request.state.db
        from app.infrastructure.repositories import CaseRepository

        storage = app.state.container.storage
        return {
            "case_state_total": CaseRepository(session).counts_by_state(),
            "job_queue_depth": JobQueue(session).active_depth(),
            "process_rss_mb": process_rss_mb(),
            "available_ram_mb": available_ram_mb(),
            "cpu_load_proxy": cpu_percent(),
            "disk_free_mb": free_disk_mb(str(storage.root)),
            "disk_case_bytes": storage.used_bytes(),
            "note": "tanpa PII atau isi bukti",
        }

    return app

