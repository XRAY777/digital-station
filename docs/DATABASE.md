# Database

SQLAlchemy models are in `backend/app/models`; Alembic migrations are in `database/migrations`. Local Windows demo uses `sqlite:///./data/station.db`; Docker uses PostgreSQL with a persistent `postgres_data` volume. Set `DATABASE_URL`, then run `.venv\Scripts\python.exe -m alembic upgrade head` before starting the API. Check for model/migration drift with `.venv\Scripts\python.exe -m alembic check`.

`python -m scripts.demo_data seed` creates `DEMO-01`, ten tracks, blocks, graph, switches, signals, seven trains, schedules, six planning operations and RBAC catalog. The seed is repeatable and creates no account/password. `python -m scripts.demo_accounts` creates local demo ADMIN/DISPATCHER accounts interactively. `python -m scripts.demo_data reset` accepts only SQLite and recreates the demo station while preserving user accounts and RBAC grants.

Core tables include station topology, trains, runs, occupancies, routes/reservations, schedules/operations, incidents/conflicts, optimization plans, events/snapshots, audit logs and sessions. State changes and events are committed together. Back up the SQLite file or PostgreSQL volume before removing the demo dataset.
