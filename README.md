# JobPilot AI

> **Статус 05.11.2026:** remediation **не завершена** (**112/115** подтверждено, **3** открыто). Единственный источник: [план и чеклисты](docs/remediation-plan.md). Dry-run / simulation signoff **не** означает live-приёмку или release approval.

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
- `TELEGRAM_ADMIN_CHAT_ID` — allowed chat (private chat ID or group ID)
- `TELEGRAM_ADMIN_USER_ID` — **required in groups**; in private chat must match your user ID
- `API_AUTH_TOKEN` — required for network API data/control routes (`/jobs`, `/stats`, `/scrape/trigger`, …)
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

| Service       | Port | Description                                              |
| ------------- | ---- | -------------------------------------------------------- |
| api           | 8000 | FastAPI REST API                                         |
| postgres      | 5434 | Structured data (host `127.0.0.1:5434` → container 5432) |
| redis         | 6379 | Celery broker                                            |
| qdrant        | 6333 | Vector memory                                            |
| celery-worker | —    | Scraping + pipeline jobs                                 |
| celery-beat   | —    | Periodic scraping                                        |
| telegram-bot  | —    | Approval UI                                              |

### Remediation & release gates

Automated remediation checklists are in [docs/remediation-plan.md](docs/remediation-plan.md). Before a permitted live Kwork send:

```bash
make verify           # checklist gates + audit matrix + unit pytest (see remediation_verify.sh)
make secret-scan      # tracked-files secret patterns (C015)
make autonomous       # full local remediation script (integration opt-in via env)
make audit-status     # regenerate docs/audit-remediation-matrix.md
make drill            # staging drills: audit, preflight, cursor-bridge policy, /ready
make redis-drill      # stop redis → /ready 503 → recover (local compose)
make dependency-drill # same for postgres
make rollout          # preflight + alembic + smoke (on DB copy / staging)
```

Release is **not** accepted while checklist items remain open (`REMEDIATION_MAX_OPEN=0 make checklist-gate` must fail until remediation is complete).

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
curl http://localhost:8000/ready
# Data/control routes require API_AUTH_TOKEN (503 if token unset, 401 if wrong):
curl -H "Authorization: Bearer $API_AUTH_TOKEN" http://localhost:8000/stats
curl -H "Authorization: Bearer $API_AUTH_TOKEN" -X POST http://localhost:8000/scrape/trigger
```

Kwork listing pause (connects budget): `KWORK_PAUSE_ENABLED`, `KWORK_PAUSE_AUTO`, `KWORK_PAUSE_CHECK_INTERVAL_HOURS` — see `.env.example`.

### 4. Telegram workflow

1. Celery scrapes jobs and runs the LangGraph pipeline.
2. Relevant high-score jobs trigger a **JobPilot AI Alert** in Telegram.
3. Use inline buttons:
    - **APPROVE** — send proposal to platform
    - **EDIT** — send revised text, then auto-send
    - **SKIP** — ignore job, record reward = 0

After the approval-path deployment, an admin can reply `/rereview` to an old approval
card, or use `/rereview proposal <pending UUID>` / `/rereview inbox <pending UUID>`.
This queues one fresh full-text review with a new confirmation step. Active,
uncertain, confirmed, or protected delivery states block reissue. See the
[operator recovery guide](docs/operator-review-reissue.md) for limits and rollout prerequisites.

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

| Method | Path                            | Description                                  |
| ------ | ------------------------------- | -------------------------------------------- |
| GET    | `/health`                       | Liveness (no deps)                           |
| GET    | `/ready`                        | Readiness (Postgres, Redis; Qdrant optional) |
| GET    | `/jobs`                         | List jobs (**auth**)                         |
| GET    | `/jobs/{id}`                    | Job details (**auth**)                       |
| POST   | `/jobs/{id}/process`            | Re-run pipeline (**auth**)                   |
| POST   | `/scrape/trigger`               | Manual scrape (**auth**)                     |
| POST   | `/chat`                         | Client reply → ChatAgent (**auth**)          |
| POST   | `/outcomes/{id}`                | Record outcome + learning (**auth**)         |
| GET    | `/stats`                        | System statistics (**auth**)                 |
| POST   | `/send-attempts/{id}/reconcile` | Reconcile after `unknown_result` (**auth**)  |

## Reward System

| Outcome | Reward |
| ------- | ------ |
| sent    | 0      |
| ignored | 0      |
| replied | +5     |
| hired   | +50    |

LearningAgent adjusts scoring weights and stores successful proposals in Qdrant.

## Scrapers

| Platform | `ENABLED_SCRAPERS` | Status                                             |
| -------- | ------------------ | -------------------------------------------------- |
| Kwork    | `kwork`            | Supported (Playwright listing + optional login)    |
| Generic  | `generic`          | Configurable CSS selectors via `GENERIC_SCRAPER_*` |
| Upwork   | `upwork`           | Experimental — may require manual fixes            |
| FL.ru    | `fl_ru`            | Experimental — may require manual fixes            |

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
OPENROUTER_SIMPLE_MODEL=auto
OPENROUTER_FALLBACK_TO_OLLAMA=true
```

Ключ создаётся на https://openrouter.ai/settings/keys.

**Режимы выбора модели**

| Значение                       | Поведение                                                                                                                                                                                                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `auto` или `openrouter/free`   | Сервис запрашивает `GET /api/v1/models`, выбирает модель с **нулевыми prompt и completion** в `pricing` (остальные поля pricing, если присутствуют, тоже должны быть 0) и достаточным text-контекстом. Список кэшируется (`OPENROUTER_MODELS_CACHE_SECONDS`). |
| Явный id (`vendor/model:free`) | Модель передаётся в API как есть; авто-проверка цены не выполняется — ответственность оператора.                                                                                                                                                              |

Суффикс `:free` **не** считается доказательством бесплатности в автоматическом режиме.

**Ollama:** `OLLAMA_MODEL=auto` и `OLLAMA_SCORING_MODEL=auto` сверяют `/api/tags` и `/api/show` (completion и контекст). Для `https://ollama.com` дополнительно читается список free usage аккаунта в settings через `OLLAMA_SESSION_COOKIE` и `OLLAMA_AID`; API-ключ и сессия должны принадлежать одному аккаунту. Требуются квота меньше 100%, нулевой pay-as-you-go balance и отключённый auto-reload; модели с предупреждением retirement исключаются. При недоступной сессии, изменении разметки или неоднозначных данных выбор блокируется; кэш до 5 минут, платный OpenAI fallback в auto отключён. Это не серверная гарантия отсутствия списаний при параллельных изменениях аккаунта или израсходовании квоты. Изменения рабочего `.env` и перезапуск для применения выполняются отдельно.

**Fallback:** при 402/429/сетевых сбоях OpenRouter может перейти на Ollama (`OPENROUTER_FALLBACK_TO_OLLAMA=true`), без скрытого перехода на платный OpenAI в этой ветке. При `OPENROUTER_FALLBACK_TO_OLLAMA=false` или пустом списке подходящих бесплатных моделей ошибка возвращается явно. Ollama при `OLLAMA_AUTO_FALLBACK` может перейти на платный `OPENAI_*` — отключайте, если нужен строго бесплатный контур.

Основные отклики и чаты используют `LLM_PROVIDER`; для OpenRouter задайте `LLM_PROVIDER=openrouter` и `OPENROUTER_MODEL=auto` или явный id.

OpenRouter использует общий `PROXY`, если задан, или отдельный
`OPENROUTER_PROXY_LIST`; список OpenAI автоматически не наследуется.
Учёт баланса OpenAI не включает OpenRouter. Журнал этапов показывает настроенное
имя (`auto(catalog)` для автоматического режима), а не каждый резервный вызов.
Эмбеддинги остаются на OpenAI и требуют отдельного `OPENAI_API_KEY`.
После изменения `.env` перезапустите приложение и Celery workers.

### Access and acceptance changes (2026-10-15)

Network API data/control routes require API_AUTH_TOKEN even with the legacy API_AUTH_REQUIRED=false setting; without a configured token they return503. Telegram groups require TELEGRAM_ADMIN_USER_ID. Simulation evidence cannot close live criteria. See [Block A implementation and configuration](docs/block-a-access-and-evidence.md).
