# API quick reference

OpenAPI schema: `/openapi.json`; interactive Swagger UI: `/docs`. `GET /health/live` checks process liveness; `GET /health` reports database/runtime readiness. Use one origin for UI and API through Vite or nginx.

| Area | Endpoints |
| --- | --- |
| Auth | `GET /api/auth/csrf`, `POST /api/auth/login`, `POST /api/auth/refresh`, `POST /api/auth/logout`, `GET /api/auth/me` |
| Station | `GET /api/dispatcher/snapshot`, `/api/station/topology`, `/api/trains`, `/api/tracks`, `/api/blocks`, `/api/switches`, `/api/signals`, `/api/simulation` |
| Commands | `POST /api/simulation/control`, `/api/routes`, `/api/routes/{id}/reserve`, `/api/routes/{id}/cancel`, `/api/switches/{id}/position` |
| Planning | `GET /api/schedules`, `POST /api/runs/{runId}/optimization`, `/api/optimization/{planId}/variants/{variantId}/accept` |
| Incidents/AI | `POST /api/dispatcher/incidents`, `/api/dispatcher/incidents/{id}/resolve`, `/api/dispatcher/ai/recommendations` |
| History | `GET /api/dispatcher/replay?timeMs=...`, `/api/dispatcher/audit`, `/api/dispatcher/reports.csv`, `/api/dispatcher/reports.pdf` |
| Realtime | `WS /ws/station/{stationId}`; send `SUBSCRIBE` with `stationId`, then receive `SNAPSHOT` and ordered `FRAME` messages |

POST/PUT commands use cookies, `Origin` and `X-CSRF-Token` from `/api/auth/csrf`. For repeatable critical commands send a unique `Idempotency-Key`. `401` means login/refresh is required, `403` means permission/Origin/CSRF denied, `409` means current state or resource conflicts with the command. Exact schemas, parameters and response codes are in OpenAPI.
