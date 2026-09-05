from __future__ import annotations

import html
from io import BytesIO
from typing import Any

from PIL import Image, ImageFile
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain.errors import InvalidFileType, InvalidStateTransition, NotFound, UploadLimitExceeded
from app.domain.models import EvidenceKind, EvidenceRecord, EvidenceStatus
from app.domain.policies import sha256_bytes
from app.domain.states import State
from app.infrastructure.repositories import EvidenceRepository
from app.infrastructure.resources import guard_resources
from app.infrastructure.storage import CaseStorage
from app.services.cases import CaseService
from app.services.ids import new_id

ImageFile.LOAD_TRUNCATED_IMAGES = False
Image.MAX_IMAGE_PIXELS = 20_000_000
MAX_TEXT_BYTES = 100_000
MAX_URL_BYTES = 4096
FROZEN_EVIDENCE_STATES = frozenset(
    {
        State.GENERATING,
        State.VERIFYING,
        State.HANDOFF_READY,
        State.RECEIPT_RECORDED,
        State.COMPLETE,
        State.PURGED,
    }
)

JPEG_MAGIC = b"\xff\xd8\xff"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF"
ALLOWED_MIME = {
    "image/jpeg": EvidenceKind.IMAGE,
    "image/png": EvidenceKind.IMAGE,
    "application/pdf": EvidenceKind.PDF,
}


def sniff_mime(header: bytes) -> str:
    if header.startswith(JPEG_MAGIC):
        return "image/jpeg"
    if header.startswith(PNG_MAGIC):
        return "image/png"
    if header.startswith(PDF_MAGIC):
        return "application/pdf"
    raise InvalidFileType("tipe berkas tidak diizinkan")


def pdf_page_count(data: bytes) -> int:
    try:
        reader = PdfReader(BytesIO(data))
        return len(reader.pages)
    except Exception:
        raise InvalidFileType("PDF tidak bisa dibaca") from None


async def read_upload_limited(upload: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        piece = await upload.read(65536)
        if not piece:
            break
        total += len(piece)
        if total > max_bytes:
            raise UploadLimitExceeded("ukuran unggahan melebihi 25 MB")
        chunks.append(piece)
    return b"".join(chunks)


def check_image_pixels(data: bytes, max_pixels: int) -> None:
    try:
        with Image.open(BytesIO(data)) as image:
            pixels = int(image.width) * int(image.height)
            if pixels > max_pixels:
                raise InvalidFileType("gambar melebihi batas piksel")
            image.verify()
    except InvalidFileType:
        raise
    except (OSError, ValueError, Image.DecompressionBombError):
        raise InvalidFileType("gambar tidak bisa dibaca") from None


class IntakeService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        storage: CaseStorage,
        cases: CaseService,
    ) -> None:
        self.session = session
        self.settings = settings
        self.storage = storage
        self.cases = cases
        self.evidence = EvidenceRepository(session)

    def _assert_evidence_mutable_and_capacity(self, case, extra_bytes: int) -> None:
        if case.state in FROZEN_EVIDENCE_STATES:
            raise InvalidStateTransition("tidak bisa mengubah bukti setelah persetujuan")
        guard_resources(self.settings, str(self.storage.root))
        existing = self.evidence.list_for_case(case.case_id)
        if len(existing) >= self.settings.max_upload_files:
            raise UploadLimitExceeded("maksimal 8 berkas")
        total = sum(item.size_bytes for item in existing) + extra_bytes
        if extra_bytes > self.settings.max_upload_bytes or total > self.settings.max_upload_bytes:
            raise UploadLimitExceeded("ukuran unggahan melebihi 25 MB")

    def upload_bytes(
        self,
        case_id: str,
        session_id: str,
        filename: str,
        data: bytes,
        *,
        kind_hint: EvidenceKind | None = None,
    ) -> EvidenceRecord:
        case = self.cases.get_owned(case_id, session_id)
        self._assert_evidence_mutable_and_capacity(case, len(data))
        mime = sniff_mime(data[:16] if len(data) >= 16 else data)
        kind = ALLOWED_MIME[mime]
        if kind_hint == EvidenceKind.RECEIPT:
            kind = EvidenceKind.RECEIPT
        page_count = 1
        if mime == "application/pdf":
            page_count = pdf_page_count(data)
            if page_count > self.settings.max_pdf_pages:
                raise InvalidFileType("PDF lebih dari 20 halaman")
        elif mime in {"image/jpeg", "image/png"}:
            check_image_pixels(data, self.settings.max_image_pixels)
        storage_key = self.storage.new_key()
        self.storage.write_atomic(case_id, storage_key, data)
        record = EvidenceRecord(
            evidence_id=new_id("ev"),
            case_id=case_id,
            kind=kind,
            original_name_display=html.escape(filename)[:255],
            storage_key=storage_key,
            mime=mime,
            size_bytes=len(data),
            sha256=sha256_bytes(data),
            page_count=page_count,
            status=EvidenceStatus.ACCEPTED,
            retention_until=case.expires_at,
        )
        self.evidence.add(record)
        self._reopen_for_new_evidence(case)
        return record

    def add_text(self, case_id: str, session_id: str, text: str) -> EvidenceRecord:
        payload = text.encode("utf-8")
        if len(payload) > MAX_TEXT_BYTES:
            raise UploadLimitExceeded("teks terlalu panjang")
        case = self.cases.get_owned(case_id, session_id)
        self._assert_evidence_mutable_and_capacity(case, len(payload))
        storage_key = self.storage.new_key()
        self.storage.write_atomic(case_id, storage_key, payload)
        record = EvidenceRecord(
            evidence_id=new_id("ev"),
            case_id=case_id,
            kind=EvidenceKind.TEXT,
            original_name_display="cerita.txt",
            storage_key=storage_key,
            mime="text/plain",
            size_bytes=len(payload),
            sha256=sha256_bytes(payload),
            page_count=1,
            status=EvidenceStatus.ACCEPTED,
            retention_until=case.expires_at,
        )
        self.evidence.add(record)
        self._reopen_for_new_evidence(case)
        return record

    def add_url(self, case_id: str, session_id: str, url: str) -> EvidenceRecord:
        payload = url.strip().encode("utf-8")
        if len(payload) > MAX_URL_BYTES:
            raise UploadLimitExceeded("URL terlalu panjang")
        case = self.cases.get_owned(case_id, session_id)
        self._assert_evidence_mutable_and_capacity(case, len(payload))
        storage_key = self.storage.new_key()
        self.storage.write_atomic(case_id, storage_key, payload)
        record = EvidenceRecord(
            evidence_id=new_id("ev"),
            case_id=case_id,
            kind=EvidenceKind.URL,
            original_name_display="url.txt",
            storage_key=storage_key,
            mime="text/uri-list",
            size_bytes=len(payload),
            sha256=sha256_bytes(payload),
            page_count=1,
            status=EvidenceStatus.ACCEPTED,
            retention_until=case.expires_at,
        )
        self.evidence.add(record)
        self._reopen_for_new_evidence(case)
        return record

    def _reopen_for_new_evidence(self, case) -> None:
        if case.state != State.INGESTING:
            self.cases.set_state(case, State.INGESTING, event_type="EVIDENCE_ACCEPTED")

    def delete_unprocessed(self, case_id: str, session_id: str, evidence_id: str) -> None:
        case = self.cases.get_owned(case_id, session_id)
        if case.state in FROZEN_EVIDENCE_STATES:
            raise InvalidStateTransition("tidak bisa hapus bukti")
        item = self.evidence.get(evidence_id)
        if item.case_id != case_id:
            raise NotFound("evidence not found")
        if item.status != EvidenceStatus.ACCEPTED:
            raise InvalidStateTransition("bukti yang sudah diproses tidak bisa dihapus")
        self.storage.delete_key(case_id, item.storage_key)
        self.evidence.delete(evidence_id)
