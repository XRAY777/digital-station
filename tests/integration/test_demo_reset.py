from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from backend.app.auth.service import password_hash
from backend.app.models import Operation, SimulationRun, Train, User
from database.connection import make_engine, make_session_factory
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[2]


def test_cli_seed_and_reset_create_fresh_run_without_deleting_accounts(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'demo.db').as_posix()}"
    environment = {**os.environ, "DATABASE_URL": url}
    commands = (
        ("alembic", "upgrade", "head"),
        ("scripts.demo_data", "seed"),
        ("scripts.demo_data", "seed"),
    )
    for module, *arguments in commands:
        result = subprocess.run(
            [sys.executable, "-m", module, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    engine = make_engine(url)
    factory = make_session_factory(engine)
    try:
        with factory.begin() as db:
            first_id = db.scalar(select(SimulationRun.id).limit(1))
            assert first_id is not None
            original_codes = set(db.scalars(select(Train.code)).all())
            db.add(
                User(
                    username="demo-admin",
                    password_hash=password_hash.hash("Administrator password 88!"),
                )
            )
        result = subprocess.run(
            [sys.executable, "-m", "scripts.demo_data", "reset"],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        with factory() as db:
            new_id = db.scalar(select(SimulationRun.id).limit(1))
            assert new_id is not None and new_id != first_id
            assert set(db.scalars(select(Train.code)).all()) == original_codes
            assert db.scalar(select(func.count()).select_from(Operation)) == 6
            assert db.scalar(select(User.id).where(User.username == "demo-admin")) is not None
    finally:
        engine.dispose()
