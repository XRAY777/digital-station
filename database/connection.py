"""Database engine and transaction-scoped sessions."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str | None = None) -> Engine:
    database_url = url or os.getenv("DATABASE_URL") or "sqlite:///./data/station.db"
    if database_url.startswith("sqlite:") and ":memory:" not in database_url:
        path = database_url.removeprefix("sqlite:///")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url, pool_pre_ping=True)
    if database_url.startswith("sqlite:"):

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection: object, _: object) -> None:
            cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
