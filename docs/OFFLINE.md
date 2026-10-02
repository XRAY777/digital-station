# Offline operation

Prepare Python, Node.js, pnpm, `uv`, `.venv`, `node_modules` and the project before disconnecting. Run `uv sync --locked`, `pnpm install --frozen-lockfile` and `pnpm --dir frontend build` while online. Keep those directories when transferring the project. `start.bat` only uses local files and SQLite. Do not use `uv sync`, `pnpm install` or `docker compose build` after disconnecting unless their caches/images were already prepared.

The dispatcher UI, simulation, auth, optimization, WebSocket, replay, audit and CSV/PDF reports work without network access. UI assets are bundled locally. Set `AI_PROVIDER=none` or `template` for a deterministic local explanation; Ollama/LM Studio require their service and model files prepared on the machine. An external provider is optional and a failure falls back to the local explanation.

For offline Docker, build/pull images online, transfer with `docker save`/`docker load`, then run `docker compose up -d --no-build`. Keep the PostgreSQL volume. Docker images were not built on this workstation unless a Docker engine is available.
