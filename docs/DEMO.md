# Demo scenario for defence

1. Run `start.bat` (prepare dependencies beforehand). On first use create ADMIN and DISPATCHER when prompted; log in as DISPATCHER. Show the Station Efficiency score and west/east approach trains.
2. In ROUTES choose train `П-3001` and destination `T02-E`; create the route, then press RESERVE. Show held blocks and switch/signal state. Press START and x10. The train moves on the live map. Switch 2D → 3D → 2D; its server state continues unchanged.
3. In DEMO COMMANDS select `П-3001`, set a two-minute delay and press **Ввести задержку**. Choose path `02` and press **Закрыть путь**. The alert, conflict and highlighted track appear; incident replanning prepares variants. Add a dispatcher note if desired.
4. Press PAUSE to keep `stateVersion` stable while choosing. Press **Рассчитать варианты**, inspect PREVIEW options, then **Спросить AI**. With `AI_PROVIDER=none`, the offline explanation is shown. Press ACCEPT; the backend repeats version and resource checks. If a 409 appears, calculate a new plan and repeat.
5. Resolve the track closure, RESUME and observe movement and Efficiency. When the first route completes, create another safe route for a waiting train. Show the audit entry for plan/AI acceptance. Move the Replay timeline to the conflict and press RETURN TO LIVE.
6. Download CSV and PDF (in the browser Downloads folder), open `/health` on backend port 8000, and show `AI OFFLINE` while all core functions remain available.

The supplied train numbers and station seed are preserved. `reset-demo.bat` restores the same dataset for another run while retaining accounts. A REST/WebSocket integration version of this flow is in `tests/integration/test_final_demo.py`.
