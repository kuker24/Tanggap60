from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.config import Settings
from app.deps import AppContainer, build_container
from app.domain.policies import sha256_bytes
from app.main import create_app
from app.services.extraction import OcrPort


class ScriptedOcr(OcrPort):
    def __init__(self) -> None:
        self.by_hash: dict[str, str] = {}

    def recognize(self, image_bytes: bytes) -> str:
        digest = sha256_bytes(image_bytes)
        if digest not in self.by_hash:
            raise RuntimeError("ocr missing script")
        return self.by_hash[digest]


def png_bytes(text: str) -> bytes:
    image = Image.new("RGB", (900, 280), "white")
    draw = ImageDraw.Draw(image)
    draw.text((24, 80), text, fill="black")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def tmp_env(tmp_path: Path) -> tuple[Settings, ScriptedOcr, AppContainer]:
    settings = Settings(
        secret_key="test-secret-key-16",
        database_url=f"sqlite:////{tmp_path / 'db' / 't.db'}",
        case_storage_dir=tmp_path / "cases",
        resource_guard_enabled=False,
        sync_jobs=True,
        app_env="test",
        official_iasc_url="https://iasc.ojk.go.id/",
    )
    ocr = ScriptedOcr()
    container = build_container(settings, ocr=ocr)
    return settings, ocr, container


@pytest.fixture
def client(tmp_env: tuple[Settings, ScriptedOcr, AppContainer]) -> TestClient:
    _settings, _ocr, container = tmp_env
    app = create_app(container)
    return TestClient(app)


@pytest.fixture
def ocr(tmp_env: tuple[Settings, ScriptedOcr, AppContainer]) -> ScriptedOcr:
    return tmp_env[1]
