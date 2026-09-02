from __future__ import annotations

import time

from app.deps import build_container, services_from
from app.infrastructure.jobs import JobQueue
from app.infrastructure.logging import configure_logging
from app.infrastructure.resources import guard_resources

LOGGER = configure_logging("tanggap60-worker")


def recover(container) -> None:  # type: ignore[no-untyped-def]
    session = container.sessions()
    try:
        n = JobQueue(session).recover_stale()
        t = container.storage.reconcile_temp()
        session.commit()
        LOGGER.info("reconcile jobs=%s temp=%s", n, t)
    except Exception:
        session.rollback()
        LOGGER.exception("reconcile failed")
    finally:
        session.close()


def loop() -> None:
    container = build_container()
    recover(container)
    while True:
        session = container.sessions()
        try:
            queue = JobQueue(session)
            job = queue.claim_next()
            if job is None:
                session.commit()
                time.sleep(0.5)
                continue
            guard_resources(container.settings, str(container.storage.root))
            orch = services_from(session, container)["orchestrator"]
            result = orch.run_until_pause(job.case_id, job.run_id)
            queue.finish(job.job_id, result, result.get("status") == "OK")
            session.commit()
        except Exception as exc:
            session.rollback()
            LOGGER.exception("job failed")
            try:
                session2 = container.sessions()
                JobQueue(session2).finish(job.job_id, {"error": type(exc).__name__}, False)  # type: ignore[name-defined]
                session2.commit()
                session2.close()
            except Exception:
                LOGGER.exception("could not mark job failed")
        finally:
            session.close()


if __name__ == "__main__":
    loop()
