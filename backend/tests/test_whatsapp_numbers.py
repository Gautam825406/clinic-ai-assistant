"""WhatsApp templates + clinic numbers (OTP) tests — PRD Appendix A, FR-11/12/13/14.

DATABASE_URL is set to in-memory SQLite BEFORE any app module is
imported. Fixtures build a fresh tenant + clinic + doctor + schedule +
patient per test.
"""
import os
import sys
from datetime import datetime, time, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # backend/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.date_parser import now_ist  # noqa: E402
from app.models import (  # noqa: E402
    Appointment, AppointmentType, BookingPayment, CallbackTicket,
    Clinic, ClinicNumber, Doctor, Notification, Patient, Schedule, Tenant,
)
from app.notifications import (  # noqa: E402
    build_daily_digest, mark_no_show, send_daily_digest, send_reminders,
)
from app.numbers import register_number, request_otp, verify_otp  # noqa: E402
from app.payments import handle_payment_webhook, create_payment_link  # noqa: E402
from app.slot_engine import book_slot, hold_slot  # noqa: E402
from app.whatsapp import (  # noqa: E402
    OUTBOX, SimulatedWhatsAppProvider, get_whatsapp, render_template,
    send_booking_summary, send_callback_alert, send_patient_confirmation,
    send_payment_link_msg, send_payment_received_msg, send_refund_msg,
    send_sms_fallback,
)

init_db()

BASE_VARS = {
    "clinic_name": "Care Plus Clinic",
    "patient_name": "Ramesh Kumar",
    "mobile": "+919876543210",
    "mobile_last4": "3210",
    "date": "kal, Thursday, 19 November",
    "time": "10:00 AM",
    "doctor_name": "Dr. Sharma",
    "reason_line": "bukhaar",
    "token_no": 3,
    "booking_amount": 100,
    "booking_amount_line": "Paid Rs. 100 (visit par adjust)",
    "total_count": 2,
    "callback_count": 1,
    "entries_text": "10:00 AM — Ramesh Kumar — bukhaar (Token 3)",
    "reason": "doctor kab aayenge?",
    "call_time": "19 Nov, 10:00 AM",
    "payment_link": "https://pay.clinic-ai-assistant.local/p/abc123",
    "hold_minutes": 15,
    "balance_fees": 400,
    "refund_status": "shuru ho gaya — refund initiate ho gaya hai",
}


@pytest.fixture
def fx():
    OUTBOX.clear()
    db = SessionLocal()
    tenant = Tenant(name="Test Clinic Tenant", type="clinic", city="Bangalore")
    db.add(tenant)
    db.commit()
    clinic = Clinic(tenant_id=tenant.id, display_name="Care Plus Clinic",
                    specialty_template_id="general",
                    whatsapp_doctor="+919900000001", whatsapp_reception="+919900000002",
                    booking_amount_enabled=True, booking_amount_default=100,
                    timely_cancel_hours=4, late_cancel_policy="forfeit",
                    hold_minutes=15, reminder_hours_before=2, no_show_grace_minutes=15)
    db.add(clinic)
    doctor = Doctor(tenant_id=tenant.id, name="Dr. Sharma", speciality="General", slot_minutes=15)
    db.add(doctor)
    db.commit()
    day = now_ist().date() + timedelta(days=1)
    db.add(Schedule(tenant_id=tenant.id, doctor_id=doctor.id, weekday=day.weekday(),
                    start_time="09:00", end_time="13:00"))
    atype = AppointmentType(tenant_id=tenant.id, name="New Consult",
                            duration_minutes=15, fees_text="Rs. 500")
    db.add(atype)
    patient = Patient(tenant_id=tenant.id, name="Ramesh Kumar", mobile="+919876543210")
    db.add(patient)
    db.commit()
    data = {"db": db, "tenant": tenant, "clinic": clinic, "doctor": doctor,
            "atype": atype, "patient": patient, "day": day}
    yield data
    for model in (BookingPayment, Notification, Appointment, CallbackTicket,
                  ClinicNumber, Patient, Schedule, AppointmentType, Doctor, Clinic, Tenant):
        db.query(model).delete()
    db.commit()
    db.close()
    OUTBOX.clear()


def _confirmed_appt(db, fx, slot):
    """Book a CONFIRMED appointment directly (no payment flow)."""
    appt = book_slot(db, fx["tenant"].id, fx["doctor"].id, fx["patient"].id,
                     slot, 15, reason_line="bukhaar",
                     appointment_type_id=fx["atype"].id,
                     booking_amount=100, payment_status="paid")
    assert appt is not None
    return appt


# --- all 7 templates render ---------------------------------------------------

@pytest.mark.parametrize("name", [
    "NEW_BOOKING", "PATIENT_CONFIRMATION", "DAILY_DIGEST", "CALLBACK_ALERT",
    "BOOKING_PAYMENT_LINK", "PAYMENT_RECEIVED", "REFUND_UPDATE",
])
def test_all_templates_render_with_placeholders_filled(name):
    text = render_template(name, dict(BASE_VARS))
    assert "{{" not in text and "}}" not in text, text
    assert text.strip()


def test_template_contents_match_prd_appendix_a():
    assert "Care Plus Clinic" in render_template("NEW_BOOKING", dict(BASE_VARS))
    assert "Token: 3" in render_template("NEW_BOOKING", dict(BASE_VARS))
    assert "Ramesh Kumar" in render_template("PATIENT_CONFIRMATION", dict(BASE_VARS))
    assert "Callbacks pending: 1" in render_template("DAILY_DIGEST", dict(BASE_VARS))
    assert "Callback chahiye" in render_template("CALLBACK_ALERT", dict(BASE_VARS))
    link = render_template("BOOKING_PAYMENT_LINK", dict(BASE_VARS))
    assert "Rs. 100" in link and "https://pay.clinic-ai-assistant.local/p/abc123" in link
    assert "cashfree" not in link.lower()  # gateway name never shown to patients
    received = render_template("PAYMENT_RECEIVED", dict(BASE_VARS))
    assert "Rs. 100 mil gaya" in received and "Token 3" in received
    assert "cashfree" not in received.lower()
    refund = render_template("REFUND_UPDATE", dict(BASE_VARS))
    assert "Rs. 100 refund" in refund and "cashfree" not in refund.lower()


def test_render_unknown_template_raises():
    with pytest.raises(ValueError):
        render_template("NOPE", {})


# --- simulated send writes Notification rows + OUTBOX --------------------------

def test_simulated_send_writes_notification_and_outbox(fx):
    db = fx["db"]
    provider = get_whatsapp(db, fx["tenant"].id)
    assert isinstance(provider, SimulatedWhatsAppProvider)
    result = provider.send("+919876543210", "PATIENT_CONFIRMATION", "hello")
    assert result["status"] == "sent"
    assert result["provider_msg_id"].startswith("simulated-")
    assert len(OUTBOX) == 1 and OUTBOX[0]["body"] == "hello"
    row = db.query(Notification).filter(
        Notification.tenant_id == fx["tenant"].id).first()
    assert row is not None and row.status == "sent"
    assert row.provider_msg_id == result["provider_msg_id"]
    assert row.template_name == "PATIENT_CONFIRMATION"


def test_helpers_send_booking_summary_and_confirmation(fx):
    db = fx["db"]
    slot = datetime.combine(fx["day"], time(10, 0))
    appt = _confirmed_appt(db, fx, slot)
    assert send_booking_summary(db, fx["tenant"], appt)["status"] == "sent"
    assert send_patient_confirmation(db, fx["tenant"], appt)["status"] == "sent"
    rows = db.query(Notification).filter(Notification.tenant_id == fx["tenant"].id).all()
    assert {r.template_name for r in rows} == {"NEW_BOOKING", "PATIENT_CONFIRMATION"}
    summary = next(r for r in rows if r.template_name == "NEW_BOOKING")
    assert "Care Plus Clinic" in summary.body and "Dr. Sharma" in summary.body
    assert "Ramesh Kumar" in summary.body and "Token: 1" in summary.body
    assert summary.to_number == "+919900000001"  # doctor WhatsApp
    confirm = next(r for r in rows if r.template_name == "PATIENT_CONFIRMATION")
    assert confirm.to_number == "+919876543210"


def test_payment_and_refund_helpers(fx):
    db = fx["db"]
    slot = datetime.combine(fx["day"], time(10, 0))
    appt = _confirmed_appt(db, fx, slot)
    assert send_payment_link_msg(
        db, fx["tenant"], appt, "https://pay.clinic-ai-assistant.local/p/x", 100)["status"] == "sent"
    received = send_payment_received_msg(db, fx["tenant"], appt, 100)
    assert received["status"] == "sent"
    assert "Rs. 400" in OUTBOX[-1]["body"]  # Rs. 500 fees - Rs. 100 = Rs. 400 balance
    assert send_refund_msg(db, fx["tenant"], appt, 100,
                           refund_status="initiated", reason_line="timely_cancel")["status"] == "sent"
    assert "REFUND_UPDATE" in {m["template_name"] for m in OUTBOX}


def test_callback_alert_helper(fx):
    db = fx["db"]
    ticket = CallbackTicket(tenant_id=fx["tenant"].id, name="Sunita",
                            mobile="+919111111111", reason="doctor kab aayenge?")
    db.add(ticket)
    db.commit()
    assert send_callback_alert(db, fx["tenant"], ticket)["status"] == "sent"
    assert "Sunita" in OUTBOX[-1]["body"]


def test_sms_fallback_stub():
    result = send_sms_fallback("+919876543210", "hello")
    assert result["status"] == "simulated"
    assert result["provider_msg_id"].startswith("simulated-sms-")


# --- daily digest + reminders + mark_no_show -----------------------------------

def test_daily_digest_and_send(fx):
    db = fx["db"]
    slot = datetime.combine(fx["day"], time(10, 0))
    _confirmed_appt(db, fx, slot)
    db.add(CallbackTicket(tenant_id=fx["tenant"].id, name="Sunita",
                          mobile="+919111111111", reason="fees?"))
    db.commit()
    digest = build_daily_digest(db, fx["tenant"].id, fx["day"])
    assert "Ramesh Kumar" in digest and "Token 1" in digest
    assert "Total: 1 | Callbacks pending: 1" in digest
    result = send_daily_digest(db, fx["tenant"].id, fx["day"])
    assert result["status"] == "sent"
    assert set(result["recipients"]) == {"+919900000001", "+919900000002"}


def test_send_reminders_within_window(fx):
    db = fx["db"]
    slot = (now_ist().replace(tzinfo=None) + timedelta(hours=1)).replace(second=0, microsecond=0)
    appt = _confirmed_appt(db, fx, slot)
    sent = send_reminders(db, fx["tenant"].id)
    assert len(sent) == 1 and sent[0]["appointment_id"] == appt.id
    row = db.query(Notification).filter(Notification.type == "reminder").first()
    assert row is not None and row.to_number == "+919876543210"
    # Second call same day must not double-send.
    assert send_reminders(db, fx["tenant"].id) == []


def test_mark_no_show_grace_and_delegation(fx):
    db = fx["db"]
    # Paid + confirmed, slot in the past, reminder sent -> no-show.
    slot = (now_ist().replace(tzinfo=None) - timedelta(hours=2)).replace(second=0, microsecond=0)
    appt = hold_slot(db, fx["tenant"].id, fx["doctor"].id, fx["patient"].id,
                     slot, 15, appointment_type_id=fx["atype"].id)
    link = create_payment_link(db, appt)
    handle_payment_webhook(db, {"gateway_payment_id": "gw-ns-1", "amount": 100,
                                "status": "SUCCESS", "order_id": link["payment_id"]})
    db.add(Notification(tenant_id=fx["tenant"].id, type="reminder",
                        to_number=fx["patient"].mobile,
                        template_name="PATIENT_CONFIRMATION", status="sent",
                        body="reminder", sent_at=now_ist().replace(tzinfo=None)))
    db.commit()
    result = mark_no_show(db, appt.id, by="reception")
    assert result["status"] == "no_show"
    db.refresh(appt)
    assert appt.status == "NO_SHOW" and appt.payment_status == "forfeited"


def test_mark_no_show_grace_not_passed(fx):
    db = fx["db"]
    slot = (now_ist().replace(tzinfo=None) + timedelta(hours=1)).replace(second=0, microsecond=0)
    appt = _confirmed_appt(db, fx, slot)
    result = mark_no_show(db, appt.id)
    assert result["status"] == "error" and result["reason"] == "grace_not_passed"


# --- OTP: happy path, wrong, expired, cross-tenant ------------------------------

def test_otp_happy_path(fx):
    db = fx["db"]
    row = register_number(db, fx["tenant"].id, "+919900000001", "voice")
    assert row.verified is False
    resp = request_otp(db, fx["tenant"].id, "+919900000001", "voice")
    assert "dev_otp" in resp and len(resp["dev_otp"]) == 6
    assert resp["note"].startswith("DEV ONLY")
    # Only the hash is stored — never the OTP itself.
    stored = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == fx["tenant"].id).first()
    assert stored.otp_hash is not None and stored.otp_hash != resp["dev_otp"]
    assert verify_otp(db, fx["tenant"].id, "+919900000001", resp["dev_otp"]) is True
    db.refresh(stored)
    assert stored.verified is True and stored.otp_hash is None


def test_otp_wrong_fails_and_counts_attempts(fx):
    db = fx["db"]
    resp = request_otp(db, fx["tenant"].id, "+919900000002", "voice")
    assert verify_otp(db, fx["tenant"].id, "+919900000002", "000000") is False
    row = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == fx["tenant"].id).first()
    assert row.otp_attempts == 1 and row.verified is False
    # The real OTP still works after one wrong attempt.
    assert verify_otp(db, fx["tenant"].id, "+919900000002", resp["dev_otp"]) is True


def test_otp_expired_fails(fx):
    db = fx["db"]
    resp = request_otp(db, fx["tenant"].id, "+919900000003", "voice")
    row = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == fx["tenant"].id).first()
    row.otp_expires_at = now_ist().replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    assert verify_otp(db, fx["tenant"].id, "+919900000003", resp["dev_otp"]) is False


def test_otp_max_attempts(fx):
    db = fx["db"]
    resp = request_otp(db, fx["tenant"].id, "+919900000004", "voice")
    for _ in range(5):
        assert verify_otp(db, fx["tenant"].id, "+919900000004", "000000") is False
    # Even the correct OTP is refused after 5 failed attempts.
    assert verify_otp(db, fx["tenant"].id, "+919900000004", resp["dev_otp"]) is False


def test_same_number_two_tenants_rejected(fx):
    db = fx["db"]
    other = Tenant(name="Other Clinic", type="clinic", city="Bangalore")
    db.add(other)
    db.commit()
    phone = "+919900000009"
    resp = request_otp(db, fx["tenant"].id, phone, "voice")
    assert verify_otp(db, fx["tenant"].id, phone, resp["dev_otp"]) is True
    with pytest.raises(ValueError):
        register_number(db, other.id, phone, "voice")
    with pytest.raises(ValueError):
        request_otp(db, other.id, phone, "voice")
    with pytest.raises(ValueError):
        verify_otp(db, other.id, phone, resp["dev_otp"])
