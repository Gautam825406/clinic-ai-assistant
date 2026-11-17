# Clinic AI Assistant

> **Clinic AI Assistant** — a Hinglish AI voice assistant that answers a clinic's
> phone, books appointments, takes a small refundable booking amount, and sends
> the doctor a WhatsApp summary + daily digest. Pilot specialty: **General / GP**.
>
> Source PRD: *Clinic AI Voice Agent — PRD v1.1* (18 Nov 2026). The PRD's working
> title is kept only as a source reference; every user-facing string in this
> product says **Clinic AI Assistant**. GitHub repo: **clinic-ai-assistant**.

## What it does

- **Voice booking (simulated + live-ready):** a caller speaks Hinglish
  ("Namaste, mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai") — the
  dialogue engine greets, takes recording consent, collects name/mobile/
  date/time, checks real slots, reads a confirm-back, and books. It never
  books without confirm-back, never gives medical advice (fixed deflection
  only), and hands emergencies to a fixed emergency script + human callback.
- **Slots are deterministic:** `slot_engine` + the DB `UNIQUE(doctor_id,
  slot_start)` decide freeness — the LLM never does.
- **Refundable booking amount (FR-26, v1.1):** clinic-configurable ON/OFF,
  Rs. 50 / Rs. 100 or custom, per appointment type. Slot is held 15 min
  pending UPI payment and becomes CONFIRMED only after the gateway webhook
  verifies payment. Attended = adjusted against visit fee; timely cancel
  (default 4h before) = full refund; late cancel = clinic policy (default
  forfeit); clinic-side cancel = always full refund; no-show = forfeit.
  Patients who cannot pay via UPI are manually confirmed by reception
  (no-UPI exception) — never by the patient claiming they paid.
- **Specialty templates (config only):** General / GP (pilot, default),
  Dental, Dermatology, Paediatrics, Physiotherapy, Custom. General:
  New Consult 15m, Follow-up 10m (booking amount exempt), Report dikhana
  10m; intake questions max 2–3 ("Problem kab se hai?", "Pehle visit hua
  tha?"); summary shows Reason line + new/follow-up flag.
- **Own number per clinic (v1.1+):** every clinic registers and OTP-verifies
  its own phone/WhatsApp number in the dashboard — never one shared number.
- **Dashboard:** single-file mobile-first UI (`backend/app/static/index.html`)
  — Today list (Aaya / Nahi aaya), manual booking, call log, callbacks,
  FR-17 config, numbers + OTP, simulated demo call, platform admin view.
- **Multi-tenant:** `tenant_id` on every table; every query filters by it;
  cross-tenant access returns 404. Tests prove Clinic A cannot read Clinic B.

## Pilot

- **Specialty:** General / GP (decided 17 Nov 2026 evening voice call).
- **Pilot clinic name/doctor (Open Decision D1): still OPEN.** The demo seed
  is a clearly-flagged placeholder only: "Care Plus Clinic — DEMO" /
  "Dr. Sharma (DEMO)". Do not treat it as a real clinic or doctor.
- **Open decisions:** `GET /api/open-decisions` lists D1–D9 from PRD Sec 19
  (+ D9 from Sec 6/FR-26). D9 default: amount ON, Rs. 100, 4h cut-off,
  late-cancel forfeit — final call with the pilot clinic.

## Run locally

```bash
cp .env.example .env        # fill ONLY what you have; never commit .env
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --app-dir backend
# open http://localhost:8000  (dashboard)  •  /docs (API)  •  /health
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
.venv/bin/python scripts/demo_call.py
```

1. Fresh DB → seeds the DEMO clinic (Mon–Sat 09:00–13:00 + 16:00–21:00).
2. Simulated call: greeting → consent → "kal shaam … bukhaar hai" →
   name/mobile → "shaam 5 baje" → confirm-back → hold (HELD_PENDING_PAYMENT).
3. Payment link (Rs. 100) → simulated gateway webhook → **CONFIRMED**,
   Token 1.
4. Prints the WhatsApp outbox (payment link, payment received, booking
   summary, patient confirmation) + the daily digest.

Or via the API: `POST /auth/signup` → save the `api_token` (shown once) →
paste it in the dashboard → **Demo Call** tab → run the simulated call.

To seed the demo clinic on server startup instead, set
`DEMO_MODE=true` **and** `SEED_DEMO=1` (default off in code — seeding is
opt-in so a production boot never creates demo data).

## .env setup

Copy `.env.example` → `.env`. Everything secret comes from the environment,
never from code:

| Group | Vars | Notes |
|---|---|---|
| Core | `APP_ENV`, `DEMO_MODE`, `DATABASE_URL`, `REDIS_URL` | SQLite file locally; Postgres (Neon) in prod |
| LLM / STT | `GROQ_API_KEY`, `GROQ_STT_MODEL`, `GROQ_LLM_MODEL` | Optional locally — simulated when empty |
| Telephony | `TELEPHONY_PROVIDER`, `TELEPHONY_API_KEY`, `TELEPHONY_API_SECRET`, `TELEPHONY_NUMBER` | Pick ONE vendor after a trial (D2) |
| WhatsApp | `WHATSAPP_PROVIDER`, `WHATSAPP_API_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID` | Via a BSP; simulated outbox when empty |
| Payments | `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY`, `CASHFREE_ENV`, `PAYMENT_WEBHOOK_SECRET` | Gateway name is never shown to patients |
| OTP | `OTP_PROVIDER`, `OTP_API_KEY` | Simulated in dev — OTP shown in dashboard/logs only |

## What is simulated vs live

| Area | Simulated (no keys) | Live (keys set) |
|---|---|---|
| Telephony | `SimulatedTelephonyAdapter`, `/voice/simulate`, WS `/voice/stream/{call_id}` | Vendor adapter (Exotel / Plivo / Twilio) via `/webhooks/telephony/call` |
| STT / TTS | Simulated (text in = text out) | Groq Whisper STT (+ TTS provider) |
| WhatsApp | In-memory outbox + `notifications` rows | BSP WhatsApp Business API |
| Payments | `https://pay.clinic-ai-assistant.local/p/<id>` links, webhook accepted in demo mode | Cashfree order + signed webhook |
| OTP | Dev OTP returned in the API response (demo mode only) | SMS/voice provider OTP, never returned |
| DB / cache | SQLite file, in-memory KV | Postgres (Neon), Redis |

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
- Meta: `GET /health`, `GET /api/open-decisions`, `GET /` (dashboard).

## Tests

```bash
.venv/bin/python -m pytest backend/tests/test_templates.py backend/tests/test_api.py -q
# 15 passed (templates + signup/auth/config/slots/booking/double-booking/
# cross-tenant 404/health/open-decisions/seed idempotency/OTP/simulated call)
```

Tests use a fresh throwaway SQLite file (see `backend/tests/conftest.py` —
plain `:memory:` does not survive the app's pooled connections, so a temp
file is used instead; same dialect, same schema).

## Unit economics — Starter Rs. 2,999/mo is a hypothesis, not a fact

PRD Sec 13.8: monthly cost per clinic = number rental + (call minutes ×
telephony rate) + (minutes × STT+TTS rate) + (calls × LLM cost) +
WhatsApp messages × rate + hosting share. Target gross margin ≥ 60%; a
clinic at 100 calls/day must still be profitable. **Validate the real
per-call cost from the pilot's first 7 days before locking the price** —
if cost is higher, adjust the plan's call limit / included minutes first,
not the price on hope.

## Pilot checklist

- [ ] **D1** — pilot clinic + doctor confirmed (name, not just specialty)
- [ ] **D2** — telephony vendor trial (1 week, 2 vendors) then lock
- [ ] **D9** — booking amount ON/OFF, Rs. 50 vs Rs. 100, 4h cut-off,
      late-cancel forfeit vs partial refund — tick with the pilot clinic
- [ ] WhatsApp templates 1–7 submitted for BSP approval (Phase 0)
- [ ] Doctor-approved greeting + emergency script wording (D6)
- [ ] One real payment test before launch (Cashfree sandbox → prod)
- [ ] 7-day pilot review with Sec 3.3 numbers → go / no-go, paid conversion

## Credential checklist (needed before live pilot)

- [ ] Telephony vendor trial account + virtual number
- [ ] WhatsApp BSP account + approved templates
- [ ] Groq API key (STT + LLM)
- [ ] Cashfree app (App ID + Secret Key) + webhook secret
- [ ] Postgres (Neon) + Redis URLs for production

## Repo layout

```
backend/app/
  main.py  config.py  database.py  models.py  security.py  schemas.py
  date_parser.py  slot_engine.py  safety.py  dialogue.py  telephony.py
  stt_tts.py  whatsapp.py  payments.py  numbers.py  notifications.py
  specialty_templates.py  seed.py
  routers/  auth.py  clinics.py  booking.py  voice.py
            payments_router.py  numbers_router.py  admin.py  common.py
  static/index.html        # dashboard (single file, no build step)
backend/tests/  test_templates.py  test_api.py  conftest.py
scripts/demo_call.py
docs/API.md  CHANGELOG.md  docker-compose.yml  Dockerfile  requirements.txt
```

## Rules (non-negotiable)

- No secrets in code or chat. No medical advice anywhere — deflection only.
- No invented real clinics, doctors, or stats. Demo data is flagged DEMO.
- The gateway name is never shown to patients.
- UPI / card details are never stored — only gateway id + status.

## Live deploy (status 18 Nov 2026)

**GitHub repo `Gautam825406/clinic-ai-assistant` — NOT yet created (blocked, see below).**
Once it exists, the intended live setup is:
- **GitHub:** https://github.com/Gautam825406/clinic-ai-assistant
- **Backend + dashboard:** Render free tier from `render.yaml` (or Vercel via `vercel.json` + `api/index.py`) — the dashboard is served by the FastAPI app itself at `/`, so one URL covers both.
- **Database:** Neon Postgres free tier (same pattern as RegLens / IntervIQ) — set `DATABASE_URL` in the host env vars. Render free tier sleeps when idle (first request 50s+); SQLite is local-dev only.

**Blocker (exact):** creating the GitHub repo from this machine fails — the GitHub integration returns `403 Resource not accessible by integration` on `create_repository`, and no local git credentials are stored. One-time unblock, any of:
1. The user creates an **empty public repo named `clinic-ai-assistant`** at https://github.com/new (no README/license/.gitignore — the push brings its own README), or
2. The parent agent creates it in the browser, then the snapshot push (`github push-files`, manifest prepared in the repo) uploads this code, after which Render (Blueprint from `render.yaml`) and Vercel (`create_git_project`) deploy from the repo.

**Live vs simulated (until real credentials are added):** the live site will run in simulated mode — demo booking, payment link, webhook and WhatsApp outbox all work end-to-end, but no real phone call, WhatsApp message or rupee moves. Still needed from the user (env vars only, never in chat/git): telephony vendor key (Exotel/Plivo/Twilio), WhatsApp BSP token, Groq key, Cashfree App ID + Secret (production only when ready), Neon Postgres URL.
