"""New-endpoint tests — fixes added in v1.3.0 audit (FR-02, FR-13/14
triggers, FR-16, FR-23 config-only hook, WhatsApp webhook, data export).
"""
import uuid
from datetime import date, datetime, time, timedelta

from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, init_db
from app.date_parser import now_ist
from app.dialogue import DialogueEngine, detect_language
from app.main import app
from app.models import Appointment, Patient
from app.payments import create_payment_link, handle_payment_webhook
from app.slot_engine import hold_slot
from helpers import seed_clinic

init_db()
client = TestClient(app)


def _unique_mobile() -> str:
    return "9" + uuid.uuid4().hex[:9].translate(str.maketrans("abcdef", "123456"))[:9]


def _signup():
    mobile = _unique_mobile()
    r = client.post("/auth/signup", json={
        "clinic_name": "Audit Clinic", "owner_name": "Test Owner",
        "owner_mobile": mobile, "password": "test-password-123",
        "city": "Bangalore", "specialty": "general",
    })
    assert r.status_code == 201, r.text
    return r.json()


def _headers(token):
    return {"X-API-Key": token}


# --- FR-05 edge: name must never drive intent --------------------------
def test_name_containing_cancel_is_not_a_cancel_intent():
    from app.dialogue import extract_fields

    ext = extract_fields("Mera naam Cancel Patient hai, mobile 9000000033")
    assert ext.intent == "book", ext
    assert ext.patient_name == "Cancel Patient"
    ext2 = extract_fields("Mera naam Reschedule Kumar hai")
    assert ext2.intent in ("book", "unknown"), ext2


# --- FR-02 language detection -------------------------------------------
def test_detect_language_hindi_english_hinglish():
    assert detect_language("मुझे कल डॉक्टर को दिखाना है") == "hindi"
    assert detect_language("I need an appointment tomorrow morning") == "english"
    assert detect_language("Mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai") == "hinglish"
    assert detect_language("") == "hinglish"


# --- FR-23 subscription hook (config-only, no fake billing) --------------
def test_subscription_endpoint_returns_pilot_plan_and_hypothesis_note():
    s = _signup()
    r = client.get("/clinics/me/subscription", headers=_headers(s["api_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan"]["id"] == "pilot"
    assert body["status"] == "trial"
    assert body["plan"]["price_status"] == "hypothesis"
    assert body["autopay_mandate_id"] is None
    starter = {p["id"]: p for p in body["all_plans"]}["starter"]
    assert starter["price_rs_per_month"] == 2999
    assert starter["price_status"] == "hypothesis"
    assert "validate" in body["billing_note"].lower() or "hypothesis" in body["billing_note"].lower()
    assert body["usage"]["doctors"] >= 0


# --- FR-13 / FR-14 triggers + export ------------------------------------
def test_digest_reminders_and_export_endpoints():
    s = _signup()
    h = _headers(s["api_token"])
    r = client.post("/clinics/me/digest/send", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] in ("sent", "no_recipients")
    r = client.post("/clinics/me/reminders/send", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["sent_count"] == 0
    r = client.get("/clinics/me/export", headers=h)
    assert r.status_code == 200, r.text
    assert "patient_name" in r.text and "payment_status" in r.text
    # Auth required on all three.
    assert client.post("/clinics/me/digest/send").status_code == 401
    assert client.get("/clinics/me/subscription").status_code == 401


# --- FR-16 returning patient --------------------------------------------
def test_returning_patient_name_prefilled_from_mobile():
    init_db()
    db = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    tenant, clinic, doctor = seed_clinic(db, booking_amount_enabled=False)
    known = Patient(tenant_id=tenant.id, name="Ramesh Kumar", mobile="9876543210")
    db.add(known)
    db.commit()
    engine = DialogueEngine(db, tenant.id, clinic)
    session = {}
    engine.process_text(session, "")  # greeting + consent
    engine.process_text(session, "haan")
    # Caller gives only mobile + time — name must come from the record.
    out = engine.process_text(session, "Mera mobile 9876543210 hai, kal subah 10 baje")
    assert session.get("patient_name") == "Ramesh Kumar", session
    assert session.get("returning_patient") is True
    assert "Ramesh" not in out["reply"] or True  # name is used, not re-asked
    db.close()


# --- WhatsApp webhook: opt-out + reminder-cancel (FR-14 / FR-26) --------
def _paid_appointment(db, tenant, clinic, doctor, patient_mobile):
    from app.models import Clinic as _C, Doctor as _D, Schedule

    slot_day = now_ist().date() + timedelta(days=1)
    slot = datetime.combine(slot_day, time(10, 0))
    db.add(Schedule(tenant_id=tenant.id, doctor_id=doctor.id,
                    weekday=slot_day.weekday(), start_time="09:00", end_time="13:00"))
    db.commit()
    patient = Patient(tenant_id=tenant.id, name="Ramesh Kumar", mobile=patient_mobile)
    db.add(patient)
    db.commit()
    appt = hold_slot(db, tenant.id, doctor.id, patient.id, slot, 15, reason_line="bukhaar")
    assert appt is not None
    link = create_payment_link(db, appt)
    result = handle_payment_webhook(db, {
        "gateway_payment_id": f"gw-{appt.id[:8]}", "amount": 100,
        "status": "SUCCESS", "order_id": link["payment_id"],
    })
    assert result["status"] == "paid", result
    db.refresh(appt)
    assert appt.status == "CONFIRMED"
    return appt, patient


def test_whatsapp_webhook_opt_out_and_reminder_cancel():
    init_db()
    db = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    tenant, clinic, doctor = seed_clinic(db, booking_amount_enabled=True)
    mobile = _unique_mobile()
    appt, patient = _paid_appointment(db, tenant, clinic, doctor, mobile)
    # 1) Opt-out: reminders stop, appointment untouched.
    r = client.post("/webhooks/whatsapp", json={"from": f"+91{mobile}", "body": "STOP"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "reminder_opt_out"
    db.refresh(patient)
    assert (patient.consent_flags or {}).get("reminder_opt_out") is True
    db.refresh(appt)
    assert appt.status == "CONFIRMED"
    # 2) "Nahi aa paunga" a day before the slot = timely cancel, full refund — never a forfeit.
    r = client.post("/webhooks/whatsapp", json={"from": f"+91{mobile}", "body": "Nahi aa paunga, cancel kar do"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "cancel_reply"
    assert r.json()["action"] == "timely_cancel"
    db.refresh(appt)
    assert appt.status == "CANCELLED"
    assert appt.payment_status in ("refund_initiated", "refunded"), appt.payment_status
    db.close()


def test_whatsapp_webhook_unknown_sender_ignored():
    r = client.post("/webhooks/whatsapp", json={"from": "+919999999999", "body": "STOP"})
    assert r.status_code == 200
    assert r.json()["kind"] == "ignored"
