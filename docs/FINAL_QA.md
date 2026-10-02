# Block H verification — 2026-10-02

Status: **PARTIALLY READY**. Existing project changes retained; no later stages started.

## Executed

- Python regression: **63 passed**, no failures/skips; `logs/final-tests.xml`.
- Frontend: **9 passed**; frozen-lockfile install, ESLint, TypeScript and production build passed.
- Backend: Ruff and strict mypy passed. Wheel built and bundled Swagger asset checked.
- Windows: actual migrations, noninteractive seed, start/health/frontend/stop/reset against a disposable SQLite database passed. Accounts survive reset. Paths containing spaces and the Windows venv interpreter child are handled.
- REST/WebSocket E2E: dispatcher login, route reservation, x10 movement, delay/closure/conflict, feasible alternatives, offline AI acceptance, audit, replay, CSV/PDF, another safe route and movement, replay during live simulation, reset/reconnect passed.
- Browser smoke: login, dashboard, WebSocket, 2D/3D, route reserve/start/x10, movement, delay/closure/conflict, optimization and offline AI acceptance, new dispatcher-created route, replay/live, CSV/PDF clicks, health and logout/session revocation checked.
- UI latency: 10 measured UI commands until the updated state version was visible: average **289 ms**, p95 **315 ms**, max **315 ms**. This is a conservative command-to-render upper bound for event-to-render; SQLite `occurred_at` has one-second resolution, so the exact event timestamp cannot establish a sub-500 ms figure.
- CSV/PDF: HTTP 200; `text/csv` / `application/pdf`; `attachment` names correct; API bodies 909 / 2,649 bytes with station/train data. Non-empty physical files also exist in the user's Downloads folder (631 / 2,148 bytes), with station and train data. In-app Browser download events did not expose a new file path.
- Performance: two moving trains, three WebSocket clients, multiple conflicts, x10, ten notes submitted by three concurrent workers. Snapshot p95 **120.1 ms**, replanning max **190.4 ms**, burst **766.5 ms**, ten position events in the initial sample. Python traced peak **4.7 MiB** (not process RSS), CPU-time/wall-time ratio **0.70**. Threshold assertions passed again in the final suite.

## Remaining verification limits

- Docker runtime and PostgreSQL deployment: **NOT VERIFIED**; Docker executable/engine unavailable.
- Physical second-PC LAN, disconnected-network cold start, external/local model service: **NOT VERIFIED**. Local fallback and localhost multi-client operation were tested.
- Exact server `occurredAt` to paint latency cannot be independently resolved below one second with SQLite's timestamp precision; measured UI command-to-render upper bound was <500 ms. Long-duration heap/listener stability: **NOT VERIFIED**.
- Clean Python dependency installation into a new environment: **NOT VERIFIED**; existing environment and isolated wheel build were used.
- Plan acceptance reserves planning resources; physical routes still require explicit dispatcher creation/reservation. The E2E verifies that explicit command through Safety, not automatic rerouting by plan acceptance.

One dependency deprecation warning remains: Starlette TestClient's httpx compatibility. No failing tests remain. Demo instructions: [DEMO.md](DEMO.md).
