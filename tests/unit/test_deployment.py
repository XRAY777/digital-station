from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_frontend_runtime_has_no_remote_asset_dependencies() -> None:
    asset_sources = [ROOT / "frontend" / "index.html"]
    asset_sources.extend((ROOT / "frontend" / "src").rglob("*.css"))
    asset_sources.extend((ROOT / "frontend" / "src").rglob("*.tsx"))
    asset_sources.extend((ROOT / "frontend" / "src").rglob("*.ts"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in asset_sources)
    for remote_asset in (
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "cdnjs.cloudflare.com",
        "unpkg.com",
        "jsdelivr.net",
    ):
        assert remote_asset not in combined
    vite = (ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    assert '"/ws": { target: backendTarget, ws: true }' in vite
    run = (ROOT / "backend" / "app" / "run.py").read_text(encoding="utf-8")
    auth = (ROOT / "backend" / "app" / "auth" / "router.py").read_text(encoding="utf-8")
    websocket = (ROOT / "backend" / "app" / "websocket" / "router.py").read_text(
        encoding="utf-8"
    )
    start = (ROOT / "scripts" / "start-demo.ps1").read_text(encoding="utf-8")
    assert 'os.getenv("STATION_HOST", "127.0.0.1")' in run
    assert "str(request.base_url).rstrip(\"/\")" in auth
    assert "str(websocket.base_url).rstrip(\"/\")" in websocket
    assert "STATION_LAN" in start and "0.0.0.0" in start


def test_windows_scripts_are_project_scoped_and_never_install_dependencies() -> None:
    start = (ROOT / "scripts" / "start-demo.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop-demo.ps1").read_text(encoding="utf-8")
    reset = (ROOT / "scripts" / "reset-demo.ps1").read_text(encoding="utf-8")
    assert "uv sync" not in start and "pnpm install" not in start
    assert "taskkill" not in stop.lower() and "python.exe" not in stop.lower()
    assert "ExecutablePath.StartsWith($root" in stop
    assert "StartsWith('sqlite:')" in reset
    assert "demo_data reset" in reset


def test_docker_migrations_are_single_run_and_database_is_persistent() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "service_healthy" in compose
    assert "service_completed_successfully" in compose
    assert "postgres_data:/var/lib/postgresql/data" in compose
    assert "migrate:" in compose
    assert "condition: service_completed_successfully" in compose
    assert "urlopen('http://127.0.0.1:8000/health'" in compose
    assert '"${STATION_HTTP_PORT:-8080}:8080"' in compose
