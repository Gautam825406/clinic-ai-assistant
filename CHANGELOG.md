# Changelog — Clinic AI Assistant

## v1.3.2 — 18 Nov 2026 — live-test fixes: booking works for a new clinic, psycopg, readable errors, no injected frames

Live test (18 Nov morning, frontend v1.3.0) found: new clinic → 0 slots on 19/20 Nov, booking failed with "Error: [object Object]", Demo Call could not confirm ("no slot free this week"), spam iframe below dashboard + landing page.
- **Booking (P0 fix):** a new doctor got NO weekly schedule, so `generate_slots()` returned []. `slot_engine.ensure_default_schedule()` now auto-creates Mon–Sat 09:00–13:00 + 16:00–21:00 (General default, same as demo seed) when a doctor is created (`POST /clinics/me/doctors` returns `schedules_created: 12`), and `generate_slots()` backfills it for doctors created before this fix — an already-live clinic becomes bookable again after redeploy, without re-creating the doctor. Slots are deduped (overlapping schedule rows listed the same slot twice); `POST /clinics/me/schedules` is idempotent for identical hours. `POST /appointments` now rejects a slot the engine does not offer with a clear 400 string ("Ye time slot khaali nahi hai…") instead of failing opaquely.
- **psycopg (P0 fix):** `requirements.txt` now lists `psycopg[binary]>=3.1` — the Render service `clinic-ai-assistant-1hdt` deploy crashed with `ModuleNotFoundError: No module named 'psycopg'` without it. (Working backend `clinic-ai-assistant-mljb` untouched.)
- **Readable errors (P0 fix):** FastAPI validation errors return `detail` as a list, and `new Error(data.detail)` rendered as "[object Object]". Frontend `errText()` now extracts a readable string (field + message) in `api()`, signup and login; `bookNow()` refuses to submit with no slot selected instead of sending an empty `slot_start`.
- **Signup form — verified present:** v1.3.1 already added Sign up / Login cards to `frontend/index.html` wired to `POST /auth/signup` + `POST /auth/login`; the live v1.3.0 site lacked them only because v1.3.1 was never pushed. This release pushes them.
- **Spam iframe:** searched `frontend/index.html`, `frontend/landing.html` and the whole repo + git history — NO iframe, ad script or third-party domain exists in our code, so the "Buy this domain" frame was injected outside the repo (host/browser). Defence added: CSP `frame-src 'none'` (meta tag on both pages + `frontend/vercel.json` security headers incl. `X-Frame-Options: DENY`) and a frame-stripper script on both pages that removes any `<iframe>` the moment it appears (the product legitimately uses zero iframes).
- Tests: 103 passed (was 102; new `test_fresh_doctor_gets_default_schedule_and_books`). Fresh-clinic local E2E (`/tmp/e2e_v132.py`, not committed): signup → doctor (no manual schedule) → slots Thu 19 Nov: 36, Fri 20 Nov: 36 → booking 201 CONFIRMED Token 1 (₹100, pending) → payment link 200 ₹100 → webhook 200 `paid` → appointment CONFIRMED / paid / Token 1 → Demo Call books 19 Nov 17:00 HELD_PENDING_PAYMENT (correct FR-26: confirms after payment) — PASS.

## v1.3.1 — 18 Nov 2026 — final overnight sweep: super-admin reachable, signup/login UI, tenant manage

Found by a full live end-to-end run (real uvicorn + HTTP, 30/30 checks PASS):
- **Super-admin was unreachable (P0 fix):** `is_platform_admin` defaulted False for every user and no code ever set it True, so `/admin/*` could never be used in production. Fixed with env-only bootstrap `ensure_platform_admin()` (`PLATFORM_ADMIN_MOBILE` + `PLATFORM_ADMIN_PASSWORD` in host env only, never in git) called from app lifespan; documented in `backend/.env.example`.
- **Admin manage (PRD Sec 13.7):** `GET /admin/tenants/{id}` (full clinic detail + counts) and `PATCH /admin/tenants/{id}` (status trial/active/past_due/paused/cancelled, plan pilot/starter/growth/clinic_plus; audit-logged; pause never deletes data). Non-admin → 403.
- **Dashboard signup/login UI:** the dashboard had only an API-key paste box — no signup or login form. Added Sign up (clinic + owner + mobile + password + city + specialty → `POST /auth/signup`, token shown once) and Login (`POST /auth/login`) cards; token is saved to localStorage.
- **Dashboard Plan card + Admin manage buttons:** Config tab shows `GET /clinics/me/subscription` (plan, usage, all plans with hypothesis note); Admin tab lists tenants with Manage → detail, Pause / Resume, plan change.
- **Dialogue edge (found by E2E):** a name containing “cancel” (“Mera naam Cancel Patient hai”) was misread as a cancel intent. `extract_fields` now strips the extracted name before the reschedule/cancel keyword check; regression test added.
- Tests: 102 passed (was 98). Live E2E script (`/tmp/e2e_clinic.py`, not committed): signup → login → dashboard → doctor/schedule/slots → manual booking → voice simulate → payment link → webhook → CONFIRMED Token 2 → today/stats/digest/reminders/export → WhatsApp “nahi aa paunga” → timely_cancel + refund (never forfeit) → super-admin list/detail/pause/resume/plan → 403 for clinic user → landing + dashboard serve — 30/30 PASS. Demo flow still CONFIRMED Token 1.

## v1.3.0 — 18 Nov 2026 — PRD audit fixes + landing page + subscription hook (config-only)

Audit against PRD v1.1 (FR-01..FR-26) — 98 pytest pass, demo flow CONFIRMED Token 1:
- **FR-02 (fixed):** `dialogue.detect_language()` — pehle utterance se Hindi / English / Hinglish detect, session par store, `/voice/simulate` response mein `language` field.
- **FR-16 (fixed):** returning patient — mobile se purana patient milne par naam auto-fill, dobara naam nahi poochha jata (`returning_patient` flag).
- **FR-13 / FR-14 (fixed — triggers):** `POST /clinics/me/digest/send` aur `POST /clinics/me/reminders/send` — scheduler/cron ab in endpoints ko call karke digest + reminders bhej sakta hai; reminder opt-out (`consent_flags.reminder_opt_out`) ko `send_reminders` respect karta hai.
- **FR-14 / FR-26 (fixed):** `POST /webhooks/whatsapp` — patient replies: opt-out (STOP / “reminder band”) aur “nahi aa paunga” cancel — timely cancel = full refund, kabhi forfeit nahi; ambiguous mobile (ek se zyada tenant) par koi action nahi.
- **FR-23 (config-only hook):** `backend/app/plans.py` — Pilot / Starter ₹2,999 / Growth ₹5,999 / Clinic+ ₹9,999 (PRD Sec 13.2 indicative bands, `price_status: hypothesis`); `GET /clinics/me/subscription` plan + usage deta hai; signup par Subscription row banti hai (plan pilot, status trial). Koi payment / autopay / invoice code mein nahi — billing future step hai (Cashfree prod keys + pilot per-call cost validation ke baad).
- **PRD Sec 10.3 (fixed):** `GET /clinics/me/export` — clinic apne appointments ka CSV export kar sakti hai (sirf apna tenant).
- **Landing page:** `frontend/landing.html` — senior-quality public page (hero, how-it-works, honest DEMO transcript mock, features, Pricing with hypothesis note, FAQ), CTA dashboard (`index.html`) + signup se juda; koi payment button live nahi. Dashboard header se “About / Landing page” link.

## v1.2.0 — 18 Nov 2026 — one repo, frontend/ + backend/ split (user decision)

- **Same repo, two halves:** `frontend/` (static dashboard) + `backend/`
  (FastAPI) in `clinic-ai-assistant`. No second repo, no rebuild — the
  v1.1.1 code moved, nothing re-implemented.
- **Frontend:** `backend/app/static/index.html` → `frontend/index.html`;
  new `frontend/config.js` holds the ONE backend URL
  (`window.__CLINIC_API_BASE__`, default `''` same-origin); every `fetch()`
  in the dashboard now goes through that base, including the two direct
  calls (`/health`, `/api/open-decisions`) that used to bypass it.
  `frontend/vercel.json` is static-site only; `frontend/README.md` documents
  the Vercel deploy (Root Directory = `frontend`).
- **Backend:** dashboard serving removed from `main.py` — `/` now returns a
  JSON pointer to the frontend; `/health` and all `/api/*` routes unchanged.
  CORS is now configurable: new `FRONTEND_ORIGIN` env var (comma-separated,
  added to `render.yaml` + `backend/.env.example`); empty means `*` in
  DEMO_MODE only, and no cross-origin access in prod. `.env.example` moved
  to `backend/.env.example`; `docs/API.md` moved to `backend/docs/API.md`.
- **Removed:** Vercel Python deploy of the backend — `api/index.py`, root
  `vercel.json`, `.vercelignore` deleted. Backend deploys on Render only
  (Blueprint `render.yaml` at repo root); frontend deploys on Vercel only.
  Root README rewritten for the two-folder layout and the two deploys.

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
