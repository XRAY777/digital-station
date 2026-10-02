# Digital Station

Демонстрационная цифровая железнодорожная станция: диспетчерский экран, серверная симуляция, маршруты, конфликты, варианты плана и история.

## Быстрый запуск на Windows

До отключения сети подготовьте Python 3.12, Node.js 22, pnpm 11, зависимости (`uv sync --locked`, `pnpm install --frozen-lockfile`) и проект на диске. Скопируйте `.env.example` в `.env`. Затем запустите `start.bat`: скрипт применит миграции, создаст demo seed, при первом запуске предложит задать пароли ADMIN и DISPATCHER, откроет браузер. Адрес: `http://127.0.0.1:5173/`. `stop.bat` останавливает процессы проекта; `reset-demo.bat` восстанавливает станцию и сохраняет учётные записи.

Для защиты войдите как созданный DISPATCHER. Последовательность действий — [DEMO.md](docs/DEMO.md). Демонстрационные пароли не поставляются. CSV/PDF формируются сервером по запросу и попадают в папку загрузок браузера; постоянного каталога отчётов нет.

## Режимы и справка

- Local/Offline/LAN/Docker/VPS: [INSTALLATION.md](docs/INSTALLATION.md), [OFFLINE.md](docs/OFFLINE.md), [DEPLOYMENT.md](docs/DEPLOYMENT.md).
- Локальный AI: `AI_PROVIDER=none|template|ollama|lmstudio|openai-compatible|external`; без провайдера работает объяснение по шаблону.
- API: `/openapi.json`, Swagger UI `/docs`, health `/health` и `/health/live` на backend `:8000` (через Docker proxy `:8080`).
- Техническая справка: [ARCHITECTURE.md](docs/ARCHITECTURE.md), [SIMULATION.md](docs/SIMULATION.md), [OPTIMIZATION.md](docs/OPTIMIZATION.md), [SECURITY.md](docs/SECURITY.md), [DATABASE.md](docs/DATABASE.md), [API.md](docs/API.md).

Проверки: `.venv\Scripts\pytest.exe -q`, `pnpm --dir frontend test -- --run`, `pnpm --dir frontend typecheck`, `pnpm --dir frontend lint`, `pnpm --dir frontend build`, `.venv\Scripts\ruff.exe check backend tests`, `.venv\Scripts\mypy.exe backend`.
