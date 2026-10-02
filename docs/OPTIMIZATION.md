# Optimization and efficiency

`POST /api/runs/{runId}/optimization` reads a committed version of operations, tracks, closures, current occupancy, maintenance and resource reservations. The deterministic engine returns up to three feasible alternatives with operation times/tracks and an explanation. It reports `TIMEOUT` or `INFEASIBLE` explicitly. Replanning after an incident creates a new plan; it does not itself move a train or open a route.

The dispatcher can preview a variant and apply it through `POST /api/optimization/{planId}/variants/{variantId}/accept`. Acceptance checks the current `stateVersion`, recomputes feasibility and reserves required resources in one transaction. If state changed, the server returns 409; request a fresh plan. Route and signal safety remains enforced independently.

The Station Efficiency Index is derived on the server from throughput, delay, utilization, conflicts and idle resources. Admins can change the weights through `/api/stations/{stationId}/efficiency-config`; the dashboard displays the current score. AI receives the current server plan and only explains/points to an allowed variant. `AI_PROVIDER=none` keeps the fallback available offline.
