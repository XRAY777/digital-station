# Simulation

The server owns the simulation clock and train positions. `POST /api/simulation/control` accepts `START`, `PAUSE`, `RESUME`, `STOP`, `RESET` and `SET_SPEED` with rates 1, 2, 5 or 10. The runtime checks the station graph every tick, computes the train head and tail footprint, updates occupancies, signals and conflicts, and commits an ordered event stream. Clients never calculate authoritative movement.

A proposed route (`POST /api/routes`) must be reserved (`POST /api/routes/{id}/reserve`) before movement. Reservation locks its blocks, conflict zones and switches atomically. The safety check prevents a second train entering an occupied/held block, moving a switch under a train, or passing a denied block or signal at x10. A newly reserved demo train gets a bounded target speed; a train without an active route remains still.

`RESET` creates a new `simulationRunId`, restores seeded infrastructure and train positions, and copies the demo schedule/operations. Historical runs remain available for replay. The Windows `reset-demo.bat` separately removes and recreates only the demo station dataset in local SQLite.
