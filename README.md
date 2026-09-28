# КАПИТАЛЬ — private multi-currency wealth tracker with an AI advisor

КАПИТАЛЬ ("KAPITAL") is a privacy-first, multi-currency personal **net-worth
tracker** with an AI advisor. Add assets in free-form text, voice, a photo, or a
spreadsheet — the app parses them, values everything in any base currency at
central-bank rates, and produces **analytical insights (never buy/sell advice)**,
what-if scenarios, goals, and more.

Bilingual UI (Russian / English), runs end-to-end with **zero API keys** in demo
mode.

> Built for relocants/expats and globally-minded users holding assets across
> several countries (cash, deposits, crypto, stocks, real estate, vehicles, debts).

---

## Features

- **Smart input** — free text / voice (Whisper) / photo (vision) / Excel·CSV import, parsed by an LLM (or a deterministic fallback in demo mode).
- **Multi-currency** — USD/EUR/BYN/GEL/RUB + crypto, normalized to a chosen base currency (ECB + national banks for BYN/GEL, CoinGecko for coins, all `Decimal`).
- **Net-worth over time** — snapshots, growth windows (7d/30d/90d/1y/all) + CAGR.
- **Assets** — cash, deposits, crypto, stocks/ETFs (live quotes), real estate & vehicles (with appreciation/depreciation), debts.
- **Read-only crypto wallet sync** — BTC/ETH/TON by public address (keyless).
- **AI advisor** — non-prescriptive insights with a disclaimer; what-if scenarios.
- **Goals, target allocation + drift, geo/jurisdiction breakdown.**
- **Security** — TOTP 2FA + recovery codes, "log out everywhere", field-level encryption (Fernet), GDPR export/delete, trusted-contact (dead-man's switch).
- **Lifecycle** — update reminders + weekly digest emails.
- **Payments** — Free / Pro / Business via Stripe (card) and CoinGate (crypto).
- **PWA**, Sentry, privacy-friendly analytics, Docker + CI/CD.

## Stack

- **Backend:** Python 3.12, FastAPI, LangGraph + LangChain (provider-agnostic LLM layer), SQLAlchemy 2.0 async (SQLite dev / Postgres prod), Alembic, Pydantic v2.
- **Frontend:** React 18 + Vite + TypeScript, Tailwind + shadcn/ui, TanStack Query, Zustand, Recharts.
- **Infra:** Docker Compose, Nginx, GitHub Actions; APScheduler (dev) / Celery + Redis (prod).

## Quick start (dev, Windows — no Docker)

```bash
# Backend
cd backend
python -m venv .venv && .venv\Scripts\pip install -r requirements.txt
copy ..\.env.example .env          # DEMO_MODE=true runs with NO API keys
.venv\Scripts\python -m alembic upgrade head
.venv\Scripts\python -m uvicorn main:app --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173 (proxies /api → :8000)
```

**Demo mode** (`DEMO_MODE=true` in `backend/.env`, or no LLM key set) makes every
LLM-backed service return canned/computed results — the whole app is usable with
zero keys and zero spend. Set a real `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY` +
adjust the `LLM_*` registry) to enable real models.

## Configuration

All runtime config is in `backend/.env` (copy from `.env.example`). Never commit
secrets — `.env` is gitignored. Keep `#` comments on their own line (dotenv pulls
an inline comment after `KEY=` into the value).

## Running as a module of a host (Jarvis)

KAPITAL stays a standalone app; a host such as **Jarvis** runs it as a process
and talks to it over HTTP. The contract is small and host-agnostic — see
[`docs/JARVIS-MODULE.md`](docs/JARVIS-MODULE.md):

- `GET /api/v1/module` — public manifest (API prefix, auth scheme, health, OpenAPI).
- **Personal API tokens** (Settings → API tokens, `POST /api/v1/account/tokens`):
  `Authorization: Bearer kp_…` works on every user endpoint; shown once, stored hashed,
  revoked explicitly (not by "log out everywhere").
- **Bring your own keys** (Settings → AI, `PUT /api/v1/account/ai`): each user can
  switch from KAPITAL AI to their own Anthropic/OpenAI/Google/Groq keys, stored
  encrypted (`FERNET_KEY`) and returned masked. In `own` mode the server keys are
  never used; a missing key means demo output for that role.

## Disclaimer

КАПИТАЛЬ is a tracking and analytics tool, **not** a financial, investment, tax or
legal advisor. See `backend/legal/disclaimer.md`.
