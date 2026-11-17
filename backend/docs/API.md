# Clinic AI Assistant — API reference (v1.1.0)

Base URL (local): `http://localhost:8000`. Interactive docs: `/docs`.

## Auth

All clinic routes require the header `X-API-Key: <api_token>`. The token is
returned **once** at signup; rotate it with `/auth/rotate-token`. No key →
`401`. A resource belonging to another tenant → `404` (never `403`, so
nothing leaks about other tenants). Admin routes need a platform-admin
user → otherwise `403`.

## Meta

### GET /health

```json
{ "status": "ok", "app": "Clinic AI Assistant", "version": "1.1.0", "mode": "simulated" }
```

`mode` is `simulated` in demo mode, `live` otherwise.

### GET /

Serves the dashboard (`backend/app/static/index.html`).

### GET /api/open-decisions

PRD Sec 19 decisions D1–D8 plus D9 (FR-26, Sec 6). Example entry:

```json
{ "id": "D1", "decision": "Pilot clinic kaunsi hai? Doctor ne haan bola hai?",
  "status": "OPEN", "default": "Bina D1 ke code likho, lekin go-live ka plan mat banao",
  "note": "Pilot specialty General / GP decide ho gayi hai; kaunsi clinic aur kaunse doctor — naam abhi open hai." }
```

## Auth

### POST /auth/signup — 201

Body:

```json
{ "clinic_name": "Care Plus Clinic", "owner_name": "Umesh Kumar",
  "owner_mobile": "9876543210", "password": "••••••••",
  "city": "Bangalore", "specialty": "general" }
```

Creates Tenant + Clinic + owner User and applies the General template by
default (`specialty` may be `general|dental|dermatology|paediatrics|
physiotherapy|custom`; unknown values fall back to `general`).
Response:

```json
{ "tenant_id": "…", "clinic_id": "…", "user_id": "…",
  "api_token": "cav_…", "clinic_name": "Care Plus Clinic",
  "specialty_template_id": "general",
  "message": "Clinic AI Assistant workspace ban gaya. Ye api_token sirf ek baar dikhega — ise safe jagah save karein." }
```

### POST /auth/login

Body: `{ "mobile": "9876543210", "password": "••••••••" }`.
Response: `{ "api_token": "cav_…", "tenant_id": "…", "user_id": "…",
"name": "…", "is_platform_admin": false }`. Wrong credentials → `401`.

### POST /auth/rotate-token

Auth required. Response: `{ "api_token": "cav_… (new)",
"message": "Naya api_token mil gaya. Purana token ab kaam nahi karega." }`

## Clinics (FR-17)

### GET /clinics/me

Auth required. Returns the caller's clinic config plus its doctors:

```json
{ "id": "…", "tenant_id": "…", "display_name": "Care Plus Clinic",
  "specialty_template_id": "general",
  "fees_text": "Consultation Rs. 500 (range, confirm at reception)",
  "greeting_text": "Namaste, …", "whatsapp_doctor": "+91-…",
  "whatsapp_reception": "+91-…", "opening_time": "09:00", "closing_time": "21:00",
  "booking_amount_enabled": true, "booking_amount_default": 100,
  "timely_cancel_hours": 4, "late_cancel_policy": "forfeit",
  "hold_minutes": 15, "reminder_hours_before": 2, "no_show_grace_minutes": 15,
  "doctors": [{ "id": "…", "name": "Dr. Sharma (DEMO)", "speciality": "General / GP", "slot_minutes": 15, "active": true }] }
```

### PATCH /clinics/me

Auth required. Any subset of: `display_name`, `specialty_template_id`,
`address`, `fees_text`, `greeting_text`, `whatsapp_doctor`,
`whatsapp_reception`, `opening_time`, `closing_time`,
`booking_amount_enabled`, `booking_amount_default`, `timely_cancel_hours`,
`late_cancel_policy`, `hold_minutes`, `reminder_hours_before`.
Changing `specialty_template_id` to a valid template also applies that
template's appointment types (unknown template → `400`). Returns the
updated clinic config.

### GET /clinics/me/subscription

Auth required. FR-23 config-only hook: clinic ka plan + usage. Response:
`{ "plan": { "id": "pilot", "name": "Pilot", "price_rs_per_month": 0,
"price_status": "hypothesis", ... }, "status": "trial",
"autopay_mandate_id": null,
"usage": { "calls_total": 0, "appointments_total": 0, "doctors": 1 },
"all_plans": [ pilot, starter (₹2,999, hypothesis), growth (₹5,999, hypothesis),
clinic_plus (₹9,999, hypothesis) ], "billing_note": "..." }`.
Prices are PRD Sec 13.2 indicative bands — no payment, autopay or
invoice is created here.

### POST /clinics/me/reminders/send

Auth required. FR-14 trigger: reminder window ke CONFIRMED appointments
ko WhatsApp reminder bhejo. Returns `{ "status": "ok", "sent_count": N, "sent": [...] }`.
Opt-out patients are skipped.

### POST /clinics/me/digest/send

Auth required. FR-13 trigger: aaj ki list ka daily digest doctor +
reception ke WhatsApp par bhejo. Returns the `send_daily_digest` result.

### GET /clinics/me/export

Auth required. PRD Sec 10.3: clinic apne appointments ka CSV export
(download) — sirf apna tenant.

### GET /clinics/me/today?day=YYYY-MM-DD

Auth required. `day` defaults to today (IST). Time-ordered appointments:

```json
{ "date": "2026-11-19", "count": 1,
  "appointments": [{ "id": "…", "doctor_name": "Dr. Sharma (DEMO)",
    "patient_name": "Rahul Verma", "patient_mobile": "9000000001",
    "date": "2026-11-19", "slot_start": "2026-11-19T17:00:00",
    "slot_end": "2026-11-19T17:15:00", "reason_line": "bukhaar",
    "status": "CONFIRMED", "source": "voice", "token_no": 1,
    "payment_status": "paid", "booking_amount": 100, "hold_expires_at": null }] }
```

### POST /clinics/me/doctors — 201

Body: `{ "name": "Dr. Sharma", "speciality": "General / GP", "slot_minutes": 15 }`.
Returns the doctor.

### POST /clinics/me/schedules — 201

Body: `{ "doctor_id": "…", "weekday": 3, "start_time": "09:00",
"end_time": "13:00", "break_start": null, "break_end": null }`.
`weekday`: 0 = Monday … 6 = Sunday. Doctor must belong to the caller's
tenant (else `404`). Returns the schedule.

### POST /clinics/me/holidays — 201

Body: `{ "doctor_id": "… | null", "date": "2026-11-20", "reason": "Diwali" }`.
Returns the holiday.

### GET /clinics/me/calls

Auth required. Latest 200 calls, newest first:
`{ "count": 0, "calls": [{ "id", "call_sid", "direction", "from_number",
"started_at", "duration_sec", "outcome", "language_detected",
"failure_reason", "transcript": [] }] }`.

### GET /clinics/me/callbacks

Auth required. `{ "count": 0, "callbacks": [{ "id", "name", "mobile",
"reason", "priority", "status": "open|done", "created_at" }] }`.

### PATCH /clinics/me/callbacks/{ticket_id}

Auth required. Body: `{ "status": "done" }` (or `"open"`). Other tenant's
ticket → `404`. Returns `{ "id": "…", "status": "done" }`.

### GET /clinics/me/dashboard-stats?day=YYYY-MM-DD

Auth required. The week's 5 FR-26 numbers for the day:

```json
{ "date": "2026-11-19", "today_total": 1, "paid_confirmed": 1,
  "visits": 0, "no_shows": 0, "forfeited_rs": 0, "refunded_rs": 0,
  "open_callbacks": 0, "calls_total": 0 }
```

## Booking

### GET /slots?doctor_id=…&date=YYYY-MM-DD[&appointment_type_id=…]

Auth required. Doctor must belong to the caller's tenant (else `404`).
Returns free slot starts from the weekly schedule minus breaks, holidays
and taken slots:

```json
{ "doctor_id": "…", "date": "2026-11-19", "duration_minutes": 15,
  "slots": ["2026-11-19T09:00:00", "2026-11-19T09:15:00", "…"] }
```

### POST /appointments — 201

Auth required. Manual / reception booking (FR-20). Reception bookings are
CONFIRMED at entry (reception has taken/confirmed payment or exempted the
patient — the no-UPI exception); a booking amount still due shows as
`payment_status: "pending"`.

Body:

```json
{ "doctor_id": "…", "patient_name": "Rahul Verma", "patient_mobile": "9000000001",
  "date": "2026-11-19", "slot_start": "2026-11-19T09:00:00",
  "reason_line": "bukhaar, 2 din se", "appointment_type_id": "… | null",
  "source": "manual" }
```

`slot_start` date must match `date`. Taken slot → `409` ("Ye slot pehle
se booked hai"). Returns the appointment (same shape as `/clinics/me/today`
entries). Voice bookings differ: they stay `HELD_PENDING_PAYMENT` until
the payment webhook confirms them.

### GET /appointments/{id}

Auth required. Other tenant's appointment → `404`. Returns the appointment.

### POST /appointments/{id}/cancel

Auth required. Cancels; the timely/late cut-off is computed from the
clinic's `timely_cancel_hours` and the FR-26 outcome rules are applied
(refund / forfeit via `payments.apply_booking_outcome`). Already
cancelled/completed/no-show → `400`. Returns the appointment (+ `outcome`
and `payment` detail).

### POST /appointments/{id}/reschedule

Auth required. Body: `{ "slot_start": "2026-11-20T10:00:00",
"date": "2026-11-20" }`. New slot must be free (else `409`). Returns the
updated appointment (status stays CONFIRMED; the payment carries forward
per FR-26 — no re-pay).

### POST /appointments/{id}/outcome

Auth required. Body: `{ "outcome": "visit_completed|timely_cancel|
late_cancel|clinic_cancel|no_show" }` (`reschedule` → use `/reschedule`).
FR-26 rules via `payments.apply_booking_outcome`: visit completed =
amount adjusted; timely cancel = full refund; late cancel = clinic policy
(default forfeit); clinic cancel = always full refund; no-show = forfeit
only if the booking was CONFIRMED and a reminder was sent. Returns the
appointment (+ `payment` detail).

## Voice

### POST /webhooks/whatsapp

No auth (vendor webhook). PRD Sec 10.1: delivery statuses + patient
replies. Opt-out (`STOP` / "reminder band") sets the patient's
`reminder_opt_out`; a "nahi aa paunga" reply cancels the next upcoming
CONFIRMED appointment via the FR-26 rules (timely = full refund).
Ambiguous mobile (more than one tenant) → no action.

### POST /webhooks/telephony/call

No auth (vendor webhook). Body: the telephony provider's inbound-call
payload. Delegates to `telephony.handle_inbound_webhook`. Returns the
adapter's response. If the telephony module is not ready → `503
"module not ready"`.

### POST /voice/simulate

Auth required. Runs the DialogueEngine turn-by-turn for a full simulated
call. Body:

```json
{ "tenant_id": "… (optional, defaults to caller's tenant)",
  "from_number": "+919000000001",
  "turns": ["Namaste", "Haan", "Mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai",
            "Mera naam Rahul Verma hai, mobile 9000000001", "shaam 5 baje",
            "Haan, confirm karein"] }
```

(`text` with a single turn is also accepted.) Response:

```json
{ "call_id": "sim-…", "tenant_id": "…",
  "transcript": [{ "role": "patient", "text": "…" }, { "role": "assistant", "text": "…" }],
  "state": "PAYMENT_PENDING", "appointment_id": "…",
  "appointment": { "…appointment shape…", "status": "HELD_PENDING_PAYMENT" } }
```

With the booking amount ON, the call ends in `PAYMENT_PENDING` (held, not
confirmed) — confirm it with `POST /payments/link` + the payment webhook.
If the dialogue module is not ready → `503 "module not ready"`.

### WS /voice/stream/{call_id}

Plain FastAPI WebSocket, text frames only. Query params: `api_key=<token>`
or `tenant_id=<id>`. Send a text turn, receive JSON:
`{ "reply": "…", "state": "…", "action": "…", "appointment_id": "…" }`.
Dialogue module not ready → one `NOT_READY` message, then close.

## Payments (FR-26)

### POST /payments/link

Auth required. Body: `{ "appointment_id": "…" }`. Creates (or reuses) a
`BookingPayment` row (`link_sent`), holds the slot for the clinic's
`hold_minutes`, and sends the payment-link WhatsApp. Response:

```json
{ "link": "https://pay.clinic-ai-assistant.local/p/…",
  "payment_id": "…", "amount": 100, "status": "link_sent" }
```

Amount 0 / booking amount OFF → `{ "link": null, "payment_id": null,
"amount": 0, "status": "not_required" }`. Simulated mode uses the local
link above; live mode creates a Cashfree order. The gateway name is never
in the link text shown to patients.

### POST /webhooks/payment

No auth (gateway webhook; signature is verified inside the payments
module when a webhook secret is configured). Body (simulated shape):

```json
{ "order_id": "<payment_id>", "gateway_payment_id": "cf_demo_0001",
  "status": "SUCCESS", "amount": 100 }
```

Cashfree-style nested payloads are also accepted. On success the
appointment becomes CONFIRMED with a token number and the payment-received
+ booking-summary + patient-confirmation WhatsApps go out. Replays are
idempotent (`already_processed`, no double token / double messages).
Wrong amount → `rejected` (appointment stays HELD). Response on success:

```json
{ "status": "paid", "payment_id": "…", "appointment_id": "…",
  "appointment_status": "CONFIRMED", "token_no": 1, "amount": 100 }
```

### POST /payments/{payment_id}/refund

Auth required. Body: `{ "reason": "timely_cancel" }`. Other tenant's
payment → `404`. Simulated mode completes the refund immediately
(`refunded`); live mode leaves it `refund_initiated` until the gateway
refund webhook. Response:
`{ "status": "refunded"|"refund_initiated", "payment_id": "…",
"amount": 100, "reason": "timely_cancel", "appointment_id": "…" }`.

## Numbers (v1.1+ own-number OTP)

### POST /numbers/request-otp

Auth required. Body: `{ "phone_number": "+919000000099", "purpose": "both" }`
(`purpose`: `voice|whatsapp|both`). Response:

```json
{ "status": "otp_sent", "phone_number": "+919000000099", "purpose": "both",
  "expires_in_seconds": 600, "dev_otp": "123456", "note": "DEV ONLY — never in production" }
```

`dev_otp` is present only in DEMO_MODE / simulated provider — never in
production. OTP: 6 digits, 10-minute expiry, max 5 attempts; only its
SHA-256 hash is stored.

### POST /numbers/verify-otp

Auth required. Body: `{ "phone_number": "+919000000099", "otp": "123456" }`.
Response: `{ "verified": true, "phone_number": "+919000000099" }`.
Wrong/expired/exhausted OTP → `verified: false` (or `400`/`429`).

### GET /numbers/me

Auth required. `{ "count": 1, "numbers": [{ "id", "tenant_id",
"phone_number", "purpose", "verified": true, "created_at" }] }`.

## Admin (platform admin only)

### GET /admin/tenants

`{ "count": 2, "tenants": [{ "id", "name", "type", "city", "plan",
"status", "clinic_name", "specialty_template_id", "appointments", "calls" }] }`.

### GET /admin/stats

`{ "tenants": 2, "appointments": 5, "calls": 3, "failed_calls": 0,
"booking_payments_paid_rs": 100 }`.

### GET /admin/failures

Calls with a `failure_reason`, newest first (max 100):
`{ "count": 0, "failures": [{ "id", "tenant_id", "call_sid",
"from_number", "started_at", "outcome", "failure_reason" }] }`.

### GET /admin/digest/{tenant_id}?date=YYYY-MM-DD

`{ "tenant_id": "…", "date": "2026-11-19", "digest": "Aaj ki list — …\n5:00 PM — Rahul Verma — … (Token 1)\nTotal: 1 | Callbacks pending: 0" }`.
