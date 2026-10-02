# Architecture

```text
Frontend (React, 2D/3D, replay)
             ↓ REST / WebSocket
Backend (FastAPI, auth, RBAC, command validation)
             ↓
Station Runtime (one server process, 250 ms tick)
             ↓
Simulation / Safety / Conflict
             ↓
Database (SQLite demo or PostgreSQL server)

Optimization ← committed station snapshot
AI           ← optimization result → explanation only
ACCEPT       → authenticated command → version and safety checks
```

The station graph in `simulation/graph` defines topology. Tracks, blocks, switches, signals and train head/tail positions reference that graph. Route reservation, movement, safety and conflict handling live in separate modules under `backend/app/services` and `simulation`. The server changes live state; the browser renders committed snapshots and event frames.

Each API command uses a database transaction. Reservation conflicts and occupied blocks are checked before committing, and critical commands can use `Idempotency-Key`. A runtime task advances RUNNING simulations. WebSocket clients receive a snapshot on connection and ordered events afterwards; reconnect requests the current run and snapshot. Replay reads historical snapshots/events and does not write live state.

`optimization/engine.py` reads an immutable input snapshot and returns bounded feasible variants or `TIMEOUT`/`INFEASIBLE`. AI may explain a server plan, but cannot issue a route or reserve infrastructure. Plan acceptance checks the current `stateVersion`, recomputes feasibility and applies through authenticated backend commands.

The demo uses one API worker. Local SQLite is for one PC; Docker uses PostgreSQL and an nginx reverse proxy. The 3D view is a projected SVG of the same station state as 2D.
