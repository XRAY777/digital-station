# Local, offline, LAN, and Docker operation

## Prepare before going offline

Install Python 3.12, Node.js 22, pnpm 11, then run `uv sync --all-groups` and
`pnpm install --frozen-lockfile` while dependencies are available. Build the UI
with `pnpm --dir frontend build`. Runtime assets are local; the UI has no font,
script, map, model, or icon CDN dependency. The single-PC profile uses SQLite at
`data/station.db`; it does not need PostgreSQL or internet access. Keep `.venv`,
`node_modules`, the built frontend, and the project directory on disk.

Copy `.env.example` to `.env`; local defaults use SQLite and no AI provider. On
first start, the script runs migrations and demo seed, then prompts for the
first administrator and dispatcher accounts. No default credentials are shipped.

## Windows demo

Run `start.bat`; it starts the backend and Vite using only the prepared local
environment, waits for both services, and opens the UI. `stop.bat` stops only
processes recorded for this project. `reset-demo.bat` is restricted to SQLite,
removes only station `DEMO-01` and its cascaded demo records, re-seeds it, and
preserves user accounts and source files.

For trusted LAN testing set `STATION_HOST=0.0.0.0`, `STATION_LAN=1` and
`STATION_ALLOWED_ORIGINS=http://<server-LAN-IP>:5173` in `.env` before starting.
From a second PC open `http://<server-LAN-IP>:5173`; the browser uses that same
host for REST and WebSocket, while Vite proxies both to the backend. Permit TCP
5173 through Windows Firewall only on the trusted Private profile. Do not
forward these ports from the router. Login, RBAC, Origin, CSRF, and WebSocket
session checks remain enabled. Use HTTPS on untrusted networks.

## Docker/PostgreSQL

Copy `.env.example` to `.env` and replace `POSTGRES_PASSWORD` with a long
URL-safe secret. Build and run with `docker compose up -d --build`; the one-shot
`migrate` service waits for PostgreSQL health, applies migrations, and seeds once
before the backend starts. PostgreSQL state persists in `postgres_data`; logs go
to Docker's bounded local log driver. Run `docker compose run --rm backend
python -m backend.app.auth.setup_admin` once to create the first account.
Create a dispatcher with `docker compose run --rm backend python -m scripts.demo_accounts`
or through the authenticated `POST /api/users` admin endpoint. The migration/seed
container never prompts for a password.
The UI and API share one origin at port 8080; open that port in the server PC's
firewall for trusted LAN clients. Do not publish the backend or database ports.

To run without internet, build or pull all images while online, then export and
transfer them with `docker save`; on the offline server use `docker load` and
`docker compose up -d --no-build`. No image pull is possible without cached
images. Optional Ollama/LM Studio/OpenAI-compatible providers use `AI_PROVIDER`
and `STATION_AI_BASE_URL`; `none`/`template` keep all core functions local.

## VPS/server profile

Use the same Compose stack on a private VPS or server with an HTTPS reverse
proxy in front of port 8080. Set a long URL-safe `POSTGRES_PASSWORD`, restrict
inbound traffic, keep PostgreSQL unexposed, and back up `postgres_data`. Set
`STATION_ALLOWED_ORIGINS` to the exact public HTTPS origin. The app must see
`X-Forwarded-Proto: https` from its trusted proxy so Secure cookies and HSTS
are enabled. Run one backend worker for the station runtime. Confirm `/health`
is HTTP 200, then create accounts through the CLI/admin API. Reports are
downloaded by the browser; the API does not persist exported files.
