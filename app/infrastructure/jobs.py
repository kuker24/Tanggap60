from __future__ import annotations

import json
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.infrastructure.db import JobRow
from app.services.cases import now_utc


class JobQueue:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(
        self,
        *,
        case_id: str,
        run_id: str,
        kind: str,
        idempotency_key: str,
        payload: dict[str, object] | None = None,
    ) -> str:
        existing = self.session.scalar(
            select(JobRow).where(JobRow.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing.job_id
        job_id = f"job-{uuid4().hex[:16]}"
        self.session.add(
            JobRow(
                job_id=job_id,
                case_id=case_id,
                run_id=run_id,
                kind=kind,
                status="pending",
                idempotency_key=idempotency_key,
                attempts=0,
                payload_json=json.dumps(payload or {}),
                result_json="{}",
                created_at=now_utc(),
            )
        )
        return job_id

    def claim_next(self) -> JobRow | None:
        row = self.session.scalar(
            select(JobRow).where(JobRow.status == "pending").order_by(JobRow.created_at)
        )
        if row is None:
            return None
        row.status = "running"
        row.attempts += 1
        row.started_at = now_utc()
        self.session.flush()
        return row

    def finish(self, job_id: str, result: dict[str, object], ok: bool) -> None:
        row = self.session.get(JobRow, job_id)
        if row is None:
            return
        row.status = "done" if ok else "failed"
        row.result_json = json.dumps(result)
        row.finished_at = now_utc()

    def stale_running(self) -> list[JobRow]:
        return list(self.session.scalars(select(JobRow).where(JobRow.status == "running")))

    def recover_stale(self) -> int:
        rows = self.stale_running()
        count = 0
        for row in rows:
            row.status = "pending"
            count += 1
        return count

    def list_for_case(self, case_id: str) -> list[JobRow]:
        return list(
            self.session.scalars(
                select(JobRow).where(JobRow.case_id == case_id).order_by(JobRow.created_at)
            )
        )

    def depth(self) -> int:
        return len(list(self.session.scalars(select(JobRow).where(JobRow.status == "pending"))))

    def delete_for_case(self, case_id: str) -> None:
        from sqlalchemy import delete

        self.session.execute(delete(JobRow).where(JobRow.case_id == case_id))

    def mark_failed_safe(self, job_id: str) -> None:
        self.session.execute(
            update(JobRow).where(JobRow.job_id == job_id).values(status="failed")
        )
