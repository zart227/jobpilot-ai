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

### 2. Start all services

```bash
docker compose up --build
```

Services:

| Service       | Port | Description                                       |
| ------------- | ---- | ------------------------------------------------- |
| api           | 8000 | FastAPI REST API                                  |
| postgres      | 5433 | Structured data (5432 занят — см. docker-compose) |
| redis         | 6379 | Celery broker                                     |
| qdrant        | 6333 | Vector memory                                     |
| celery-worker | —    | Scraping + pipeline jobs                          |
| celery-beat   | —    | Periodic scraping                                 |
| telegram-bot  | —    | Approval UI                                       |

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

## API Endpoints

| Method | Path                 | Description               |
| ------ | -------------------- | ------------------------- |
| GET    | `/health`            | Health check              |
| GET    | `/jobs`              | List jobs                 |
| GET    | `/jobs/{id}`         | Job details               |
| POST   | `/jobs/{id}/process` | Re-run pipeline for job   |
| POST   | `/scrape/trigger`    | Manual scrape             |
| POST   | `/chat`              | Client reply → ChatAgent  |
| POST   | `/outcomes/{id}`     | Record outcome + learning |
| GET    | `/stats`             | System statistics         |

## Reward System

| Outcome | Reward |
| ------- | ------ |
| sent    | 0      |
| ignored | 0      |
| replied | +5     |
| hired   | +50    |

LearningAgent adjusts scoring weights and stores successful proposals in Qdrant.

## Adding a Scraper

1. Create `app/scrapers/your_platform.py` extending `BaseScraper`.
2. Register in `app/scrapers/registry.py`.
3. Add platform name to `ENABLED_SCRAPERS` in `.env`.

## Local Development (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium

# Start infrastructure
docker compose up postgres redis qdrant -d

python scripts/init_db.py
uvicorn app.main:app --reload
celery -A app.celery_app worker --loglevel=info
celery -A app.celery_app beat --loglevel=info
python -m app.telegram.bot
```

## Environment Variables

See `.env.example` for the full list.

## License

MIT
