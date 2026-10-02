from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import pytest
from backend.app.auth.service import password_hash
from backend.app.models import Role, SimulationRun, User, UserRole
from database.connection import make_engine, make_session_factory
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[2]


def _free_port(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


@pytest.mark.skipif(sys.platform != "win32", reason="Windows demo scripts")
def test_start_stop_and_reset_windows_demo_against_temporary_database(tmp_path: Path) -> None:
    if not shutil.which("powershell.exe") or not shutil.which("node.exe"):
        pytest.skip("PowerShell or Node.js unavailable")
    if not _free_port(8000) or not _free_port(5173):
        pytest.skip("Demo ports already in use")
    url = f"sqlite:///{(tmp_path / 'windows-demo.db').as_posix()}"
    environment = {
        **os.environ,
        "DATABASE_URL": url,
        "STATION_NO_BROWSER": "1",
        "STATION_HOST": "127.0.0.1",
        "STATION_BACKEND_URL": "http://127.0.0.1:8000",
    }
    for module, arguments in (
        ("alembic", ("upgrade", "head")),
        ("scripts.demo_data", ("seed",)),
    ):
        prepared = subprocess.run(
            [sys.executable, "-m", module, *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert prepared.returncode == 0, prepared.stderr
    engine = make_engine(url)
    factory = make_session_factory(engine)
    with factory.begin() as db:
        original_run = db.scalar(select(SimulationRun.id).limit(1))
        assert original_run is not None
        for role_code in ("ADMIN", "DISPATCHER"):
            role = db.scalar(select(Role).where(Role.code == role_code))
            assert role is not None
            user = User(
                username=f"qa-{role_code.lower()}",
                password_hash=password_hash.hash("Temporary demo test password 88!"),
            )
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=role.id))

    def powershell(name: str) -> subprocess.CompletedProcess[str]:
        # Detached Windows children can inherit anonymous pipes and keep communicate() open.
        with (tmp_path / f"{name}.log").open("w+", encoding="utf-8") as output:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(ROOT / "scripts" / name),
                ],
                cwd=ROOT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=90,
                check=False,
            )
            output.seek(0)
            return subprocess.CompletedProcess(result.args, result.returncode, "", output.read())

    try:
        started = powershell("start-demo.ps1")
        assert started.returncode == 0, started.stderr
        with urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
            assert response.status == 200
        with urlopen("http://127.0.0.1:5173/", timeout=3) as response:
            assert response.status == 200
        stopped = powershell("stop-demo.ps1")
        assert stopped.returncode == 0, stopped.stderr
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not _free_port(8000):
            time.sleep(0.1)
        assert _free_port(8000) and _free_port(5173)
        reset = powershell("reset-demo.ps1")
        assert reset.returncode == 0, reset.stderr
        with factory() as db:
            new_run = db.scalar(select(SimulationRun.id).limit(1))
            assert new_run is not None and new_run != original_run
            assert db.scalar(select(User.id).where(User.username == "qa-dispatcher")) is not None
    finally:
        powershell("stop-demo.ps1")
        engine.dispose()
