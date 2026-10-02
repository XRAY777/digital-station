import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic
from typing import Literal

from database.connection import make_engine, make_session_factory
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.dispatcher import router as dispatcher_router
from backend.app.api.history import router as history_router
from backend.app.api.planning import router as planning_router
from backend.app.api.station import router as station_router
from backend.app.auth.router import router as auth_router
from backend.app.auth.router import users_router
from backend.app.schemas.health import HealthResponse
from backend.app.services.runtime_tick import advance_simulation_tick
from backend.app.services.station_state import demo_station, latest_run
from backend.app.services.topology import demo_yard
from backend.app.websocket.router import router as websocket_router

logger = logging.getLogger(__name__)


async def _runtime_loop(application: FastAPI) -> None:
    factory = make_session_factory(application.state.db_engine)
    previous = monotonic()
    while True:
        await asyncio.sleep(0.25)
        current = monotonic()
        elapsed_ms = min(2_000, max(1, round((current - previous) * 1000)))
        previous = current
        try:
            with factory.begin() as db:
                station = demo_station(db)
                yard = demo_yard(db)
                run = latest_run(db, station.id) if station is not None else None
                if run is not None and yard is not None and run.status == "RUNNING":
                    advance_simulation_tick(db, run, yard, elapsed_ms)
            application.state.runtime_fault = None
        except Exception as exc:  # keep the API alive while readiness reports runtime degradation
            application.state.runtime_fault = type(exc).__name__
            logger.exception("Simulation runtime tick failed")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(_runtime_loop(application), name="station-runtime")
    application.state.runtime_task = task
    application.state.runtime_fault = None
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Digital Station API",
    description="Digital station API with server-authoritative movement and safety engines.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
)
app.mount(
    "/static/docs",
    StaticFiles(directory=Path(__file__).parent / "static" / "docs"),
    name="docs-static",
)
database_engine = make_engine()
app.state.db_engine = database_engine

_rate_windows: dict[tuple[str, str], list[float]] = {}
_RATE_LIMITS: dict[str, tuple[int, float]] = {
    "/api/auth/refresh": (30, 60),
    "/api/dispatcher/ai/recommendations": (12, 60),
    "/api/dispatcher/reports.csv": (12, 60),
    "/api/dispatcher/reports.pdf": (12, 60),
}


@app.middleware("http")
async def security_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response: Response
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > 1_048_576:
        response = JSONResponse({"detail": "request_too_large"}, status_code=413)
    else:
        rule = _RATE_LIMITS.get(request.url.path)
        if request.method == "POST" and request.url.path.startswith(
            ("/api/routes", "/api/switches", "/api/dispatcher/incidents", "/api/simulation")
        ):
            rule = (90, 60)
        if rule is None and request.url.path == "/api/auth/login":
            rule = (120, 60)
        if rule is not None:
            key = (request.url.path, request.client.host if request.client else "unknown")
            now = monotonic()
            samples = [stamp for stamp in _rate_windows.get(key, []) if now - stamp < rule[1]]
            if len(samples) >= rule[0]:
                response = JSONResponse(
                    {"detail": "rate_limited"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(rule[1] - (now - samples[0]))))},
                )
            else:
                samples.append(now)
                _rate_windows[key] = samples
                if len(_rate_windows) > 10_000:
                    _rate_windows.clear()
                response = await call_next(request)
        else:
            response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self' ws: wss:; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        ),
    )
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    if request.url.path.startswith(("/api/auth", "/api/")):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(station_router)
app.include_router(dispatcher_router)
app.include_router(history_router)
app.include_router(planning_router)
app.include_router(websocket_router)


@app.get("/docs", include_in_schema=False)
def swagger_docs() -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Digital Station API</title>"
        "<link rel='icon' href='/static/docs/favicon-32x32.png'>"
        "<link rel='stylesheet' href='/static/docs/swagger-ui.css'>"
        "</head><body><div id='swagger-ui'></div>"
        "<script src='/static/docs/swagger-ui-bundle.js'></script>"
        "<script src='/static/docs/station-swagger.js'></script></body></html>"
    )


@app.get("/health/live", tags=["health"])
def liveness() -> dict[str, str]:
    return {"status": "ok", "service": "station-api"}


@app.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse, "description": "Required services are not ready"}},
    tags=["health"],
)
def readiness() -> JSONResponse:
    try:
        with app.state.db_engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        database_state: Literal["ok", "unavailable"] = "ok"
    except SQLAlchemyError:
        database_state = "unavailable"
    runtime_fault = getattr(app.state, "runtime_fault", None)
    simulation_state: Literal["ok", "degraded"] = (
        "ok" if database_state == "ok" and runtime_fault is None else "degraded"
    )
    ready = database_state == "ok" and simulation_state == "ok"
    state = HealthResponse(
        status="ready" if ready else "not_ready",
        backend="ok",
        database=database_state,
        simulation=simulation_state,
        optimizer="ok",
        websocket="ok",
        ai="offline",
    )
    return JSONResponse(
        status_code=200 if ready else 503,
        content=state.model_dump(mode="json"),
        headers={} if ready else {"Retry-After": "5"},
    )


@app.get("/api/stage", tags=["system"])
def current_stage() -> dict[str, str]:
    return {
        "stage": "34",
        "name": "final testing performance documentation and demo",
        "status": "partially_ready",
    }
