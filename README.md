# JobPilot AI

AI-powered freelance automation system for a single developer. JobPilot AI scrapes job boards, filters and scores opportunities with AI, generates personalized proposals, routes them through Telegram for human approval, sends approved proposals, handles client chat, and learns from outcomes.

## Architecture

```
Job → FilterAgent → ScoringAgent → ProposalAgent → Telegram Approval
    → Send → ChatAgent → LearningAgent
```

**Stack:** FastAPI · LangGraph · Celery · Redis · PostgreSQL · Qdrant · Playwright · aiogram · Cursor SDK

## Project Structure

```
jobpilot-ai/
├── app/
│   ├── agents/          # Filter, Scoring, Proposal, Chat, Learning + LangGraph
│   ├── db/              # SQL schema + SQLAlchemy models
│   ├── llm/             # OpenAI / Anthropic abstraction
│   ├── memory/          # Qdrant vector store
│   ├── scrapers/        # Playwright scrapers (generic, kwork)
│   ├── services/        # Pipeline, rewards, proposal sender
│   ├── tasks/           # Celery tasks
│   ├── telegram/        # Bot with APPROVE / EDIT / SKIP
│   ├── main.py          # FastAPI API
│   └── celery_app.py
├── docker-compose.yml
├── Dockerfile
├── scripts/init_db.py
└── .env.example
```

## Quick Start

### 1. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set:

- `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` — if using those providers
- `CURSOR_API_KEY` + `LLM_PROVIDER=cursor` — [Cursor SDK](https://cursor.com/docs/sdk/python) (recommended)
- `TELEGRAM_BOT_TOKEN` — from [@BotFather](https://t.me/BotFather)
- `TELEGRAM_ADMIN_CHAT_ID` — your Telegram user/chat ID
- `DEVELOPER_*` — your profile for proposal generation
- Scraper URLs and credentials (Kwork optional)

### 2. Start all services (Docker)

```bash
./scripts/docker-up.sh
```

This starts the Cursor bridge on the host (for `/dev` Telegram agent) and then `docker compose up`.

Manual start without bridge helper:

```bash
docker compose up --build
```

Services:

| Service       | Port | Description                                       |
| ------------- | ---- | ------------------------------------------------- |
| api           | 8000 | FastAPI REST API                                  |
| postgres      | 5434 | Structured data (host `127.0.0.1:5434` → container 5432) |
| redis         | 6379 | Celery broker                                     |
| qdrant        | 6333 | Vector memory                                     |
| celery-worker | —    | Scraping + pipeline jobs                          |
| celery-beat   | —    | Periodic scraping                                 |
| telegram-bot  | —    | Approval UI                                       |

### Remediation & release gates

Automated remediation checklists are documented in [docs/remediation-closeout.md](docs/remediation-closeout.md). Before a permitted live Kwork send:

```bash
make verify        # checklist + audit + unit tests
make autonomous    # full local run: ruff, 176 pytest, drills, rollout smoke
make audit-status  # regenerate docs/audit-remediation-matrix.md
make drill         # staging drills: audit, preflight, cursor-bridge policy, /ready
make redis-drill   # stop redis → /ready 503 → recover (local compose)
make dependency-drill  # same for postgres
make rollout       # preflight + alembic + smoke (on DB copy / staging)
```

Integration tests must use a separate PostgreSQL database whose name ends in
`_test`. Set `DATABASE_URL` explicitly, and set `REDIS_URL`, `CELERY_BROKER_URL`
and `CELERY_RESULT_BACKEND` to Redis databases 2 or higher. The test runner
rejects the working database and broker to prevent test notifications entering
the live Telegram queue.

Live Kwork prep: [docs/live-kwork-prep.md](docs/live-kwork-prep.md).

Set `PLATFORM_DRY_RUN=true` in `.env` until live acceptance is explicitly approved ([docs/kwork-live-acceptance.md](docs/kwork-live-acceptance.md)).

### Production profile

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

API runs without `--reload`; code comes from the image only. Writable state: `./data`.

Operational docs: [docs/runbook.md](docs/runbook.md), [docs/ops-logging-policy.md](docs/ops-logging-policy.md).

### 3. Verify

```bash
curl http://localhost:8000/health
curl http://localhost:8000/stats
curl -X POST http://localhost:8000/scrape/trigger
```

### 4. Telegram workflow

1. Celery scrapes jobs and runs the LangGraph pipeline.
2. Relevant high-score jobs trigger a **JobPilot AI Alert** in Telegram.
3. Use inline buttons:
    - **APPROVE** — send proposal to platform
    - **EDIT** — send revised text, then auto-send
    - **SKIP** — ignore job, record reward = 0

### 5. Remote dev agent (Telegram → Cursor)

Talk to a local Cursor agent from Telegram to debug, check status, or change the project remotely.

**Commands (admin chat only):**

| Command       | Description                       |
| ------------- | --------------------------------- |
| `/dev`        | Enable dev-agent mode             |
| `/dev_status` | Project health without full agent |
| `/dev_reset`  | Start a fresh agent session       |
| `/dev_stop`   | Exit dev-agent mode               |

After `/dev`, send plain-text tasks, e.g. «проверь логи celery», «исправь ошибку в proposal_sender».

**Setup for Docker (recommended):**

1. Set `CURSOR_API_KEY` and `TELEGRAM_DEV_AGENT_ENABLED=true` in `.env`.
2. Set `CURSOR_WORKSPACE=/home/you/jobpilot-ai` (absolute host path).
3. Start everything: `./scripts/docker-up.sh` — bridge credentials go to `data/cursor_bridge.env` automatically.
4. In Telegram: `/dev` → send tasks.

If bridge stops, restart: `python scripts/run_cursor_bridge.py` then `docker compose restart telegram-bot`.

## API Endpoints

| Method | Path                 | Description               |
| ------ | -------------------- | ------------------------- |
| GET    | `/health`            | Liveness (no deps)        |
| GET    | `/ready`             | Readiness (Postgres, Redis; Qdrant optional) |
| GET    | `/jobs`              | List jobs                 |
| GET    | `/jobs/{id}`         | Job details               |
| POST   | `/jobs/{id}/process` | Re-run pipeline for job   |
| POST   | `/scrape/trigger`    | Manual scrape             |
| POST   | `/chat`              | Client reply → ChatAgent  |
| POST   | `/outcomes/{id}`     | Record outcome + learning |
| GET    | `/stats`             | System statistics         |
| POST   | `/send-attempts/{id}/reconcile` | Read-only reconcile after `unknown_result` (auth) |

## Reward System

| Outcome | Reward |
| ------- | ------ |
| sent    | 0      |
| ignored | 0      |
| replied | +5     |
| hired   | +50    |

LearningAgent adjusts scoring weights and stores successful proposals in Qdrant.

## Scrapers

| Platform | `ENABLED_SCRAPERS` | Status |
| -------- | ------------------ | ------ |
| Kwork | `kwork` | Supported (Playwright listing + optional login) |
| Generic | `generic` | Configurable CSS selectors via `GENERIC_SCRAPER_*` |
| Upwork | `upwork` | Experimental — may require manual fixes |
| FL.ru | `fl_ru` | Experimental — may require manual fixes |

## Adding a Scraper

1. Create `app/scrapers/your_platform.py` extending `BaseScraper`.
2. Register in `app/scrapers/registry.py`.
3. Add platform name to `ENABLED_SCRAPERS` in `.env`.

## Local Development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-lock.txt && pip install --no-deps -e ".[dev]"
# or: pip install -e ".[dev]"  then regenerate lock with pip freeze
playwright install chromium

# Start infrastructure
docker compose up postgres redis qdrant -d

python scripts/migration_preflight.py   # optional, before upgrade on existing data
python scripts/init_db.py               # alembic upgrade head
uvicorn app.main:app --reload
celery -A app.celery_app worker --loglevel=info
celery -A app.celery_app beat --loglevel=info
python -m app.telegram.bot
```

## Environment Variables

See `.env.example` for the full list.

## License

MIT

### Модели по задачам

`OLLAMA_MODEL=nemotron-3-nano:30b` используется для фильтрации, обучения и сводок переписки. `OLLAMA_SCORING_MODEL=gpt-oss:120b` используется для оценки заказов. Отклики, ответы клиентам и правки текстов используют основной `LLM_PROVIDER=openai` и существующий `OPENAI_MODEL`. При исчерпании лимита Ollama оценка переключается на `OPENAI_MODEL`, рутина на `OPENAI_SIMPLE_MODEL`, если включён `OLLAMA_AUTO_FALLBACK`. После изменения настроек перезапустите приложение и Celery workers.


### OpenRouter и бесплатные модели

Для бесплатной фильтрации, обучения и сводок задайте в `.env`:

```dotenv
OPENROUTER_API_KEY=ваш-ключ
LLM_SIMPLE_PROVIDER=openrouter
OPENROUTER_SIMPLE_MODEL=openrouter/free
OPENROUTER_FALLBACK_TO_OLLAMA=true
```

Ключ создаётся на https://openrouter.ai/settings/keys. `openrouter/free` выбирает
доступную бесплатную модель; для стабильного поведения можно указать конкретный
идентификатор с `:free` из актуального каталога OpenRouter. Не добавляйте `:free`
к произвольной модели: бесплатный вариант должен существовать в каталоге.

Основные отклики, чаты и оценка заказов при таком выборе используют `LLM_PROVIDER`.
Чтобы перевести их на OpenRouter, задайте `LLM_PROVIDER=openrouter` и
`OPENROUTER_MODEL=openrouter/free`. Когда `LLM_SIMPLE_PROVIDER=ollama`, оценка
по-прежнему использует `OLLAMA_SCORING_MODEL`, а резерв Ollama — OpenAI.

При HTTP 402/429, временном сбое сети/сервера, пустом или обрезанном ответе
OpenRouter переключается на `OLLAMA_MODEL`, если резерв включён. В этой ветке
переход Ollama на платный OpenAI отключён. Ошибки ключа не маскируются резервом.
Для строгого режима без резерва задайте `OPENROUTER_FALLBACK_TO_OLLAMA=false`.
Проверка JSON агентами сохраняется; некорректный JSON обрабатывается существующей
валидацией. Бесплатные модели имеют квоты и могут быть недоступны при нагрузке.

OpenRouter использует общий `PROXY`, если задан, или отдельный
`OPENROUTER_PROXY_LIST`; список OpenAI автоматически не наследуется.
Учёт баланса OpenAI не включает OpenRouter. Журнал этапов показывает настроенную
модель, а не фактическую модель бесплатного маршрутизатора или резерва.
Эмбеддинги остаются на OpenAI и требуют отдельного `OPENAI_API_KEY`.
После изменения `.env` перезапустите приложение и Celery workers.
