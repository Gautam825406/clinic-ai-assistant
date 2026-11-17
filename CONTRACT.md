# Clinic AI Assistant — build contract (PRD v1.1)

Display name: **Clinic AI Assistant**. GitHub repo: **clinic-ai-assistant**.
Workspace folder (already created): `~/workspace/clinic-ai-voice-agent/`
— internal folder name only; every user-facing string says "Clinic AI Assistant".

## Already built (do not rewrite)
- `backend/app/config.py` — Settings from env only, simulated mode when empty
- `backend/app/database.py` — SQLAlchemy engine, get_db, init_db, KVStore (Redis or in-memory)
- `backend/app/models.py` — all tables, tenant_id on EVERY table, UNIQUE(doctor_id, slot_start)
- `backend/app/security.py` — PBKDF2 passwords, api_token auth (X-API-Key), require_tenant, audit()
- `backend/app/schemas.py` — Signup/Login/Doctor/Schedule/ClinicConfig/Appointment/BookingExtraction/OTP/Payment
- `backend/app/date_parser.py` — parse_date_hint, parse_time_hint, parse_datetime_hint, format_confirm_date, IST
- `backend/app/slot_engine.py` — generate_slots, is_slot_free, hold_slot (120s), book_slot, find_alternatives (2), release_expired_holds

## Function signatures sub-builders MUST match
- safety.py: `detect_emergency(text)->bool`, `detect_advice_request(text)->bool`, `deflection_response(lang="hinglish")->str`, `emergency_script(lang="hinglish")->str`, `EMERGENCY_PATTERNS`, `ADVICE_PATTERNS`
- dialogue.py: `extract_fields(text, clinic_name="")->BookingExtraction` (rule-based; optional Groq LLM only if GROQ_API_KEY set, never required), `class DialogueEngine: __init__(db, tenant_id, clinic); process_text(session: dict, text: str) -> dict{reply, state, action, appointment_id?}` — states GREET, CONSENT, INTENT, COLLECT, CHECK_SLOTS, CONFIRM_BACK, BOOK, NOTIFY, CLOSE, HUMAN, EMERGENCY. Must call safety first, must call slot_engine for slots, must never book without confirm-back, must never answer FAQ from anywhere except clinic config.
- telephony.py: `class TelephonyAdapter` (protocol: `answer(call_sid, from_number)->dict`, `speak(text)->str`), `SimulatedTelephonyAdapter`, `get_telephony_adapter()->TelephonyAdapter`, `handle_inbound_webhook(payload: dict, db) -> dict`
- stt_tts.py: `class STTProvider` (`transcribe(audio_bytes)->str`), `SimulatedSTT`, `GroqWhisperSTT` (only when GROQ_API_KEY set), `class TTSProvider` (`synthesize(text)->bytes`), `SimulatedTTS`, `get_stt()`, `get_tts()`
- whatsapp.py: `render_template(name, vars)->str` for templates 1-7 (Appendix A names: NEW_BOOKING, PATIENT_CONFIRMATION, DAILY_DIGEST, CALLBACK_ALERT, BOOKING_PAYMENT_LINK, PAYMENT_RECEIVED, REFUND_UPDATE), `class WhatsAppProvider` (`send(to, template_name, body)->dict{status, provider_msg_id}`), `SimulatedWhatsAppProvider` (writes Notification rows + in-memory OUTBOX list), `get_whatsapp(db)`, helpers `send_booking_summary(db, tenant, appointment)`, `send_patient_confirmation(db, tenant, appointment)`, `send_payment_link_msg(db, tenant, appointment, link, amount)`, `send_payment_received_msg(...)`, `send_refund_msg(...)`, `send_callback_alert(...)`
- payments.py: `booking_amount_for(clinic, appointment_type=None)->int`, `create_payment_link(db, appointment)->dict{link, payment_id, amount}`, `verify_webhook_signature(raw_body: bytes, signature: str)->bool`, `handle_payment_webhook(db, payload: dict)->dict` (idempotent, replay-safe via idempotency_key + gateway_payment_id), `initiate_refund(db, payment_id, reason)->dict`, `apply_booking_outcome(db, appointment_id, outcome)->dict` where outcome in visit_completed|timely_cancel|late_cancel|clinic_cancel|no_show|reschedule (FR-26 rules exactly), `PAYMENT_GATEWAY_NAME="cashfree"` — never show gateway name to patients.
- numbers.py: `request_otp(db, tenant_id, phone_number, purpose="voice")->dict{dev_otp?}` (dev OTP returned ONLY when DEMO_MODE/OTP simulated; prod sends via provider), `verify_otp(db, tenant_id, phone_number, otp)->bool`, `register_number(db, tenant_id, phone_number, purpose)->ClinicNumber`
- notifications.py: `build_daily_digest(db, tenant_id, day)->str`, `send_daily_digest(db, tenant_id, day)->dict`, `send_reminders(db, tenant_id)->list`, `mark_no_show(db, appointment_id, by="reception")->dict` (only if CONFIRMED + reminder sent + no cancel + grace passed)
- specialty_templates.py: `TEMPLATES: dict[code, dict{name, appointment_types[{name,duration_minutes,fees_text,prep_line,booking_amount_override}], intake_questions[<=3], summary_fields[]}]` for general, dental, dermatology, paediatrics, physiotherapy, custom.
- seed.py: `seed_demo(db)` — ONE demo clinic only, clearly flagged DEMO ("Care Plus Clinic — DEMO"), 1 doctor, Mon-Sat 09:00-13:00 + 16:00-21:00, deletable; `delete_demo(db)`.

## Rules
- No secrets in code. No medical advice anywhere — deflection only. No invented stats/doctors.
- Every DB query filters by tenant_id. Every router uses get_current_user + require_tenant.
- Simulated mode must work with zero credentials; live mode is a config change, not a rewrite.
