# Changelog — Clinic AI Assistant

## v1.1.0 — 18 Nov 2026 — initial build

- FastAPI app "Clinic AI Assistant" (PRD v1.1): CORS open for local dev,
  `GET /health`, `GET /` dashboard, `GET /api/open-decisions` (D1–D9),
  `/static` mount, startup `init_db()` + opt-in demo seed
  (`DEMO_MODE=true` **and** `SEED_DEMO=1`).
- Specialty templates (`specialty_templates.py`): general (pilot, first and
  complete), dental, dermatology, paediatrics, physiotherapy, custom;
  `get_template()` + idempotent `apply_template_to_tenant()`.
  General: New Consult 15m, Follow-up 10m (booking amount exempt),
  Report dikhana 10m; intake "Problem kab se hai?", "Pehle visit hua tha?";
  summary `reason_line` + `new_or_followup`.
- Demo seed (`seed.py`): one DEMO clinic only — "Care Plus Clinic — DEMO",
  General template, "Dr. Sharma (DEMO)", Mon–Sat 09:00–13:00 + 16:00–21:00,
  fees "Consultation Rs. 500 (range, confirm at reception)", dummy WhatsApp
  numbers, booking amount ON Rs. 100. Random one-time demo password
  (printed once, never hardcoded). Idempotent; `delete_demo()` removes it.
- Routers: auth (signup/login/rotate-token), clinics (FR-17 config, today,
  doctors, schedules, holidays, calls, callbacks, dashboard-stats), booking
  (slots, manual booking, cancel, reschedule, outcome via
  `payments.apply_booking_outcome`), voice (telephony webhook, simulated
  call, WS text stream), payments (link, webhook, refund), numbers (OTP
  request/verify, my numbers), admin (tenants, stats, failures, digest).
  Tenant-scoped routes use `get_current_user` + `require_tenant`;
  cross-tenant access returns 404. Sibling modules are imported lazily;
  a missing sibling returns 503 "module not ready" instead of crashing
  startup.
- Dashboard (`backend/app/static/index.html`): single-file, mobile-first,
  vanilla JS, no build step, no external CDN. API-key login (localStorage
  only); tabs Today / Book / Calls / Callbacks / Config / Numbers /
  Demo Call / Admin. Hinglish labels.
- Demo script (`scripts/demo_call.py`): fresh DB → seed → simulated call →
  payment link → webhook → CONFIRMED → WhatsApp outbox + daily digest.
- Tests: 15 passing (`test_templates.py` + `test_api.py`) — templates,
  signup/auth, config, slots, manual booking, double-booking rejection,
  cross-tenant 404, health, open decisions, seed idempotency, OTP flow,
  simulated call.
- Docs: README, CHANGELOG, `docs/API.md`.

## v1.1+ — own-number OTP (added in v1.1, after FR-26)

- Every clinic has its OWN phone/WhatsApp number, verified via OTP in its
  dashboard — never one shared number across clinics (`numbers.py`,
  `ClinicNumber` model, `POST /numbers/request-otp`,
  `POST /numbers/verify-otp`, `GET /numbers/me`).
- OTP: 6 digits, 10-minute expiry, max 5 attempts, SHA-256 hash stored
  only. Dev OTP is returned only in DEMO_MODE / simulated provider —
  never in production.
- Dashboard "Numbers" tab: register number, request + verify OTP, verified
  badge.

## v1.1.1 — 18 Nov 2026 (deploy prep)
- Product name locked: Clinic AI Assistant (repo clinic-ai-assistant); D8 closed.
- Deploy prep: render.yaml (Render Blueprint, free tier + free Postgres), vercel.json + api/index.py (Vercel Python entrypoint), .vercelignore.
- Startup moved from deprecated @app.on_event to lifespan handler.
- GitHub/Render/Vercel live deploy BLOCKED pending repo creation: the GitHub integration returns 403 on create_repository and no local git credentials exist — the empty repo Gautam825406/clinic-ai-assistant must be created once (user or browser), then push + deploy proceed.

## v1.1.2 — 18 Nov 2026 (dialogue fixes found by live smoke test)
- First-turn booking intent/entities now survive the consent step (GREET stashes extraction; consent resumes the job instead of re-asking intent).
- Period memory: "kal shaam" + later bare "5 baje" resolves to 17:00, not 05:00.
- "Haan / sahi hai" after 2 slot alternatives picks the first (nearest) one.
- 3 regression tests added (92 tests total, all passing).
