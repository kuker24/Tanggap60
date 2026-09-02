from __future__ import annotations

from app.deps import build_container, services_from


def main() -> None:
    container = build_container()
    session = container.sessions()
    try:
        n = services_from(session, container)["purge"].purge_expired()
        session.commit()
        print(f"purged={n}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
