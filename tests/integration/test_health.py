import asyncio

from backend.app.main import app
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy.exc import SQLAlchemyError


def get(path: str) -> Response:
    async def send() -> Response:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(send())


def test_process_liveness_is_separate_from_service_readiness() -> None:
    response = get("/health/live")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.json() == {"status": "ok", "service": "station-api"}


def test_readiness_reports_local_runtime_and_optional_ai() -> None:
    response = get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "backend": "ok",
        "database": "ok",
        "simulation": "ok",
        "optimizer": "ok",
        "websocket": "ok",
        "ai": "offline",
    }


def test_openapi_and_stage_endpoint_are_available() -> None:
    schema = get("/openapi.json")
    docs = get("/docs")
    swagger_asset = get("/static/docs/station-swagger.js")

    assert schema.status_code == 200
    assert docs.status_code == 200
    assert "cdn." not in docs.text
    assert "/static/docs/swagger-ui-bundle.js" in docs.text
    assert swagger_asset.status_code == 200
    assert "/health" in schema.json()["paths"]
    assert "200" in schema.json()["paths"]["/health"]["get"]["responses"]
    assert get("/api/stage").json() == {
        "stage": "34",
        "name": "final testing performance documentation and demo",
        "status": "partially_ready",
    }


def test_request_body_limit_returns_4xx_and_api_responses_are_not_cached() -> None:
    async def send() -> tuple[Response, Response]:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            large = await client.post("/api/auth/login", headers={"Content-Length": "1048577"})
            csrf = await client.get("/api/auth/csrf")
            return large, csrf

    large, csrf = asyncio.run(send())
    assert large.status_code == 413
    assert csrf.headers["cache-control"] == "no-store"


def test_readiness_degrades_when_application_database_is_unavailable() -> None:
    class UnavailableEngine:
        def connect(self) -> None:
            raise SQLAlchemyError("database unavailable")

    previous = app.state.db_engine
    app.state.db_engine = UnavailableEngine()
    try:
        response = get("/health")
        assert response.status_code == 503
        assert response.json()["database"] == "unavailable"
        assert response.json()["simulation"] == "degraded"
    finally:
        app.state.db_engine = previous
