from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app.api.router import api
from app.deps import AppContainer, build_container
from app.domain.errors import AppError
from app.infrastructure.db import CaseRow
from app.infrastructure.jobs import JobQueue
from app.infrastructure.logging import configure_logging
from app.infrastructure.resources import available_ram_mb, cpu_percent, free_disk_mb, process_rss_mb
from app.session import COOKIE, error_body, get_session_id, set_session_cookie
from app.web.routes import web

LOGGER = configure_logging("tanggap60-web")
STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app(container: AppContainer | None = None) -> FastAPI:
    app = FastAPI(title="SatuAman Tanggap60", version="2.0.0")
    app.state.container = container or build_container()
    app.include_router(api)
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
            response = JSONResponse(
                status_code=exc.http_status,
                content=error_body(exc.code, exc.message, exc.recoverable, request_id),
            )
        except Exception:
            db.rollback()
            LOGGER.exception("unhandled", extra={"request_id": request_id})
            accept = request.headers.get("accept", "")
            if "text/html" in accept and not str(request.url.path).startswith("/api/"):
                response = HTMLResponse(
                    "<!doctype html><html lang='id'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'><meta name='color-scheme' content='dark'><title>Terjadi gangguan</title></head><body style=\"font-family:Archivo,system-ui,sans-serif;background:#100904;color:#ffedd7;max-width:40rem;margin:3rem auto;padding:0 1.25rem;line-height:1.5\"><h1 style=\"font-weight:500;text-transform:uppercase\">Sedang ada gangguan</h1><p>Coba muat ulang halaman ini. Data Anda belum terkirim ke bank atau polisi.</p><p><a href='/' style=\"color:#ffedd7\">Kembali ke beranda</a></p></body></html>",
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
        if COOKIE not in request.cookies:
            pass
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
        return JSONResponse({"status": "ready"})

    @app.get("/demo/metrics")
    def metrics(request: Request) -> dict[str, object]:
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



