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
        job_id = None
        case_id = None
        run_id = None
        session = container.sessions()
        try:
            job = JobQueue(session).claim_next()
            if job is None:
                session.commit()
                time.sleep(0.5)
                continue
            job_id = job.job_id
            case_id = job.case_id
            run_id = job.run_id
            session.commit()
        except Exception:
            session.rollback()
            LOGGER.exception("job claim failed")
            time.sleep(0.5)
            continue
        finally:
            session.close()

        if not job_id or not case_id or not run_id:
            continue
        session = container.sessions()
        try:
            guard_resources(container.settings, str(container.storage.root))
            orch = services_from(session, container)["orchestrator"]
            result = orch.run_until_pause(case_id, run_id)
            JobQueue(session).finish(job_id, result, result.get("status") == "OK")
            session.commit()
        except Exception as exc:
            session.rollback()
            LOGGER.exception("job failed")
            try:
                session2 = container.sessions()
                JobQueue(session2).finish(job_id, {"error": type(exc).__name__}, False)
                session2.commit()
                session2.close()
            except Exception:
                LOGGER.exception("could not mark job failed")
        finally:
            session.close()


if __name__ == "__main__":
    loop()
