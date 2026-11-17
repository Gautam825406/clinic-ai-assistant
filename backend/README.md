# Clinic AI Assistant — Backend

FastAPI backend for the clinic voice agent: deterministic slot engine,
LangGraph-style Hinglish dialogue, WhatsApp summaries, Cashfree refundable
booking amount (FR-26), per-clinic own-number OTP verification, specialty
templates, multi-tenant SaaS. Product: **Clinic AI Assistant**. Pilot
specialty: **General / GP**. Frontend is the static site in `../frontend/`
(same repo, `clinic-ai-assistant`).

## Layout (this folder)

```
app/            FastAPI app (main.py, config.py, database.py, models.py,
                slot_engine.py, dialogue.py, safety.py, payments.py,
                numbers.py, whatsapp.py, telephony.py, stt_tts.py,
                specialty_templates.py, seed.py, routers/)
tests/          pytest suite (see below)
docs/API.md     full API reference
.env.example    every env var (all values empty — never commit real keys)
```

Root-level files that belong to this backend: `../requirements.txt`,
`../render.yaml` (Render Blueprint), `../Dockerfile`,
`../docker-compose.yml`, `../evals/` (150-case eval set).

## Run locally

```bash
# from the repo root:
cp backend/.env.example .env   # fill ONLY what you have; never commit .env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DEMO_MODE=true SEED_DEMO=1 .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
# API on http://localhost:8000 — /health • /docs • /api/open-decisions
# The dashboard is NOT served here anymore: serve ../frontend/ separately
# (see ../frontend/README.md) and point its config.js at http://localhost:8000
```

With zero credentials the app runs in **simulated mode**: simulated
telephony, simulated WhatsApp outbox, simulated payment links, Groq STT/LLM
off. Live mode is a config change (env vars), not a rewrite.

### Docker Compose

```bash
docker compose up --build
# api on :8000, Postgres on :5432, Redis on :6379
```

### Demo flow (simulated, end to end)

```bash
.venv/bin/python scripts/demo_call.py   # from the repo root
```

Fresh DB → seeds the DEMO clinic → simulated call → hold
(HELD_PENDING_PAYMENT) → payment link (Rs. 100) → simulated webhook →
**CONFIRMED**, Token 1 → WhatsApp outbox + daily digest printed.

Or via the API: `POST /auth/signup` → save the `api_token` (shown once) →
paste it in the dashboard → **Demo Call** tab → run the simulated call.

## Env vars

Copy `backend/.env.example` → `.env`. Everything secret comes from the
environment, never from code:

| Group | Vars | Notes |
|---|---|---|
| Core | `APP_ENV`, `DEMO_MODE`, `DATABASE_URL`, `REDIS_URL` | SQLite file locally; Postgres (Neon) in prod |
| CORS | `FRONTEND_ORIGIN` | Comma-separated Vercel frontend URL(s). Empty = `*` in DEMO_MODE only; set it in prod |
| LLM / STT | `GROQ_API_KEY`, `GROQ_STT_MODEL`, `GROQ_LLM_MODEL` | Optional locally — simulated when empty |
| Telephony | `TELEPHONY_PROVIDER`, `TELEPHONY_API_KEY`, `TELEPHONY_API_SECRET`, `TELEPHONY_NUMBER` | Pick ONE vendor after a trial (D2) |
| WhatsApp | `WHATSAPP_PROVIDER`, `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Via a BSP; simulated outbox when empty |
| Payments | `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_ENV`, `PAYMENT_WEBHOOK_SECRET` | Gateway name is never shown to patients |
| OTP | `OTP_PROVIDER`, `OTP_API_KEY` | Simulated in dev — OTP shown in dashboard/logs only |

## Deploy (Render, from this same repo)

Render Blueprint file is `../render.yaml` at the **repo root** (Render reads
it from the root). In Render: **New → Blueprint → connect repo
`Gautam825406/clinic-ai-assistant`** — it creates the free web service
(`clinic-ai-assistant`, `uvicorn app.main:app`, `PYTHONPATH=backend`) +
free Postgres (`clinic-ai-assistant-db`, wired as `DATABASE_URL`).

Then in **Render → Environment** set (values only, never in git):

- `FRONTEND_ORIGIN` = the live Vercel frontend URL (e.g.
  `https://clinic-ai-assistant.vercel.app`) — required in prod, otherwise
  the browser blocks the dashboard's API calls (CORS).
- Real keys only when the user supplies them: `GROQ_API_KEY`,
  `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `PAYMENT_WEBHOOK_SECRET`,
  `TELEPHONY_API_KEY`, `WHATSAPP_API_TOKEN`. Until then the live backend
  runs in simulated/demo mode (`DEMO_MODE=true`, `SEED_DEMO=1` in
  `render.yaml`) — full demo booking/payment/WhatsApp flow, no real call,
  WhatsApp message, or rupee moves.

Render free sleeps after 15 min idle (first request ~50 s). Note the live
URL after deploy and put it in `../frontend/config.js`.

## API

Full reference: [`docs/API.md`](docs/API.md). Highlights:

- Auth: `POST /auth/signup` (returns `api_token` once), `POST /auth/login`,
  `POST /auth/rotate-token`. All other clinic routes need `X-API-Key`.
- Clinic: `GET/PATCH /clinics/me`, `GET /clinics/me/today`,
  `POST /clinics/me/doctors|schedules|holidays`, `GET /clinics/me/calls|callbacks`,
  `GET /clinics/me/dashboard-stats`.
- Booking: `GET /slots`, `POST /appointments`, `GET /appointments/{id}`,
  `POST /appointments/{id}/cancel|reschedule|outcome`.
- Voice: `POST /webhooks/telephony/call`, `POST /voice/simulate`,
  `WS /voice/stream/{call_id}`.
- Payments: `POST /payments/link`, `POST /webhooks/payment`,
  `POST /payments/{id}/refund`.
- Numbers: `POST /numbers/request-otp|verify-otp`, `GET /numbers/me`.
- Admin (platform admin only): `GET /admin/tenants|stats|failures`,
  `GET /admin/digest/{tenant_id}`.
- Meta: `GET /health`, `GET /api/open-decisions`, `GET /` (API pointer —
  the dashboard is the frontend, not this service).

## Tests

```bash
.venv/bin/python -m pytest backend/tests/ -q
```

Tests use a fresh throwaway SQLite file (see `backend/tests/conftest.py` —
plain `:memory:` does not survive the app's pooled connections, so a temp
file is used instead; same dialect, same schema).

## Rules (non-negotiable)

- No secrets in code or chat. No medical advice anywhere — deflection only.
- No invented real clinics, doctors, or stats. Demo data is flagged DEMO.
- The gateway name is never shown to patients.
- UPI / card details are never stored — only gateway id + status.
- Slot booking and payment decisions are deterministic code + DB only —
  never the LLM. The LLM only extracts fields.
