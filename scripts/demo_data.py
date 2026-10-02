"""Seed or safely reset the single-PC DEMO-01 dataset."""

from __future__ import annotations

import argparse
import os

from backend.app.models import Station
from database.connection import make_engine, make_session_factory
from database.seed.demo import seed_demo
from sqlalchemy import delete, select


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "reset"))
    action = parser.parse_args().action
    url = os.getenv("DATABASE_URL", "sqlite:///./data/station.db")
    if action == "reset" and not url.startswith("sqlite:"):
        raise SystemExit("Demo reset is restricted to SQLite; refusing to clear a server database.")
    engine = make_engine(url)
    factory = make_session_factory(engine)
    try:
        with factory.begin() as db:
            if action == "reset":
                station_id = db.scalar(select(Station.id).where(Station.code == "DEMO-01"))
                if station_id is not None:
                    db.execute(delete(Station).where(Station.id == station_id))
            seed_demo(db)
        print("Demo database seeded.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
