from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.hermes.adapter import HermesPort, build_hermes
from app.hermes.tool_registry import ToolContext
from app.infrastructure.db import init_db, make_engine, session_factory
from app.infrastructure.storage import CaseStorage
from app.services.approval import ApprovalService
from app.services.artifacts import ArtifactService
from app.services.cases import CaseService
from app.services.extraction import NullOcr, OcrPort, TesseractOcr
from app.services.inspect import InspectService
from app.services.intake import IntakeService
from app.services.orchestrator import Orchestrator
from app.services.purge import PurgeService
from app.services.receipt import ReceiptService
from app.services.review import ReviewService
from app.services.verifier import VerifierService


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def configure_settings(settings: Settings) -> None:
    get_settings.cache_clear()

    @lru_cache(maxsize=1)
    def _inner() -> Settings:
        return settings

    globals()["get_settings"] = _inner  # used in tests via override


@dataclass
class AppContainer:
    settings: Settings
    engine: Engine
    sessions: sessionmaker[Session]
    storage: CaseStorage
    hermes: HermesPort
    ocr: OcrPort


def build_container(settings: Settings | None = None, ocr: OcrPort | None = None) -> AppContainer:
    cfg = settings or get_settings()
    Path(cfg.case_storage_dir).mkdir(parents=True, exist_ok=True)
    engine = make_engine(cfg.database_url)
    init_db(engine)
    storage = CaseStorage(cfg.case_storage_dir, cfg.case_storage_quota_bytes)
    storage.reconcile_temp()
    ocr_port: OcrPort = ocr if ocr is not None else _default_ocr()
    return AppContainer(
        settings=cfg,
        engine=engine,
        sessions=session_factory(engine),
        storage=storage,
        hermes=build_hermes(cfg),
        ocr=ocr_port,
    )


def _default_ocr() -> OcrPort:
    try:
        return TesseractOcr()
    except Exception:
        return NullOcr()


def services_from(session: Session, container: AppContainer) -> dict[str, object]:
    cases = CaseService(session, container.settings, container.storage)
    intake = IntakeService(session, container.settings, container.storage, cases)
    inspect = InspectService(session, container.settings, container.storage, cases, container.ocr)
    approval = ApprovalService(session, cases)
    artifacts = ArtifactService(session, container.settings, container.storage, approval)
    receipt = ReceiptService(session, cases, container.storage)
    purge = PurgeService(session, cases, container.storage, container.ocr)
    review = ReviewService(session, cases, approval)
    verifier = VerifierService(session, container.storage)
    ctx = ToolContext(inspect, artifacts, approval, receipt, container.settings.official_iasc_url)
    orch = Orchestrator(session, container.settings, cases, container.hermes, ctx)
    return {
        "cases": cases,
        "intake": intake,
        "inspect": inspect,
        "approval": approval,
        "artifacts": artifacts,
        "receipt": receipt,
        "purge": purge,
        "review": review,
        "verifier": verifier,
        "orchestrator": orch,
        "ctx": ctx,
    }
