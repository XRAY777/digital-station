# Installation

## Windows local demo

Install Python 3.12, Node.js 22, pnpm 11 and `uv` while online. From the project directory run `uv sync --locked` and `pnpm install --frozen-lockfile`. Copy `.env.example` to `.env`; keep the default SQLite URL for a single-PC demo. Run `start.bat`. First start asks for ADMIN and DISPATCHER usernames/passwords without storing plaintext credentials. Then open `http://127.0.0.1:5173/`.

`stop.bat` stops the recorded project processes. `reset-demo.bat` re-seeds only `DEMO-01` in SQLite; it retains accounts. If dependencies are missing, prepare them before running the scripts; startup never installs packages. Use `docs/DEMO.md` after login.

## Manual development

Run `.venv\Scripts\python.exe -m alembic upgrade head`, then `.venv\Scripts\python.exe -m scripts.demo_data seed`. For accounts run `.venv\Scripts\python.exe -m scripts.demo_accounts`. Start `.venv\Scripts\python.exe -m backend.app.run` and `pnpm --dir frontend dev` in separate terminals. The Vite server proxies REST and WebSocket to the backend.

For a server install use [DEPLOYMENT.md](DEPLOYMENT.md). Verify `/health` returns HTTP 200 before signing in.
