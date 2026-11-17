"""FR-26 payment tests — PRD v1.1 Sec 18.2: all 12 payment cases.

DATABASE_URL is set to in-memory SQLite BEFORE any app module is
imported (config reads it at import time). Fixtures build a fresh
tenant + clinic + doctor + schedule + patient per test.
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
    Appointment, AppointmentType, BookingPayment, CallbackTicket, Clinic,
    ClinicNumber, Doctor, Notification, Patient, Schedule, Tenant,
)
from app.payments import (  # noqa: E402
    booking_amount_for, create_payment_link, handle_payment_webhook,
    initiate_refund, manual_confirm, apply_booking_outcome,
    verify_webhook_signature,
)
from app.slot_engine import book_slot, hold_slot, is_slot_free, release_expired_holds  # noqa: E402
from app.whatsapp import OUTBOX  # noqa: E402

init_db()


def _tomorrow_slot(hour=10, minute=0):
    day = now_ist().date() + timedelta(days=1)
    return datetime.combine(day, time(hour, minute))


def _soon_slot(hours=1):
    return (now_ist().replace(tzinfo=None) + timedelta(hours=hours)).replace(second=0, microsecond=0)


def _past_slot(hours=2):
    return (now_ist().replace(tzinfo=None) - timedelta(hours=hours)).replace(second=0, microsecond=0)


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
    weekday = _tomorrow_slot().weekday()
    db.add(Schedule(tenant_id=tenant.id, doctor_id=doctor.id, weekday=weekday,
                    start_time="09:00", end_time="13:00"))
    db.add(Schedule(tenant_id=tenant.id, doctor_id=doctor.id,
                    weekday=now_ist().weekday(), start_time="09:00", end_time="21:00"))
    atype = AppointmentType(tenant_id=tenant.id, name="New Consult",
                            duration_minutes=15, fees_text="Rs. 500")
    db.add(atype)
    patient = Patient(tenant_id=tenant.id, name="Ramesh Kumar", mobile="+919876543210")
    db.add(patient)
    db.commit()
    data = {"db": db, "tenant": tenant, "clinic": clinic, "doctor": doctor,
            "atype": atype, "patient": patient}
    yield data
    for model in (BookingPayment, Notification, Appointment, CallbackTicket,
                  ClinicNumber, Patient, Schedule, AppointmentType, Doctor, Clinic, Tenant):
        db.query(model).delete()
    db.commit()
    db.close()
    OUTBOX.clear()


def _held(db, fx, slot, atype=None):
    appt = hold_slot(db, fx["tenant"].id, fx["doctor"].id, fx["patient"].id,
                     slot, 15, reason_line="bukhaar",
                     appointment_type_id=(atype.id if atype else fx["atype"].id))
    assert appt is not None and appt.status == "HELD_PENDING_PAYMENT"
    return appt


def _paid(db, fx, slot, atype=None):
    """HELD -> payment link -> successful webhook -> CONFIRMED."""
    appt = _held(db, fx, slot, atype)
    link = create_payment_link(db, appt)
    assert link["amount"] == 100
    assert link["link"].startswith("https://pay.clinic-ai-assistant.local/p/")
    result = handle_payment_webhook(db, {
        "gateway_payment_id": f"gw-{appt.id[:8]}", "amount": 100,
        "status": "SUCCESS", "order_id": link["payment_id"],
    })
    assert result["status"] == "paid", result
    db.refresh(appt)
    assert appt.status == "CONFIRMED" and appt.token_no == 1
    assert appt.payment_status == "paid"
    return appt, link["payment_id"]


def _add_reminder(db, fx, appt):
    db.add(Notification(tenant_id=fx["tenant"].id, type="reminder",
                        to_number=fx["patient"].mobile,
                        template_name="PATIENT_CONFIRMATION", status="sent",
                        body="reminder", sent_at=now_ist().replace(tzinfo=None)))
    db.commit()


# --- amount config ---------------------------------------------------------

def test_booking_amount_for(fx):
    clinic = fx["clinic"]
    assert booking_amount_for(clinic) == 100
    assert booking_amount_for(clinic, fx["atype"]) == 100  # no override
    override = AppointmentType(tenant_id=fx["tenant"].id, name="Follow-up",
                               duration_minutes=10, booking_amount_override=50)
    assert booking_amount_for(clinic, override) == 50
    exempt = AppointmentType(tenant_id=fx["tenant"].id, name="Free Follow-up",
                             duration_minutes=10, booking_amount_override=0)
    assert booking_amount_for(clinic, exempt) == 0
    clinic.booking_amount_enabled = False
    assert booking_amount_for(clinic) == 0
    assert booking_amount_for(None) == 0


# --- 1. pay success ----------------------------------------------------------

def test_pay_success(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "paid" and payment.gateway_payment_id is not None
    # Payment Received + booking summary + patient confirmation all sent.
    templates = [n.template_name for n in
                 db.query(Notification).filter(Notification.tenant_id == fx["tenant"].id).all()]
    assert "PAYMENT_RECEIVED" in templates
    assert "NEW_BOOKING" in templates
    assert "PATIENT_CONFIRMATION" in templates


# --- 2. pay fail -------------------------------------------------------------

def test_pay_fail(fx):
    db = fx["db"]
    appt = _held(db, fx, _tomorrow_slot())
    link = create_payment_link(db, appt)
    result = handle_payment_webhook(db, {
        "gateway_payment_id": "gw-fail-1", "amount": 100,
        "status": "FAILED", "order_id": link["payment_id"],
    })
    assert result["status"] == "failed"
    db.refresh(appt)
    assert appt.status == "HELD_PENDING_PAYMENT"  # stays HELD, never CONFIRMED
    assert appt.token_no is None


# --- 3. hold expire frees the slot ------------------------------------------

def test_hold_expire_frees_slot(fx):
    db = fx["db"]
    slot = _tomorrow_slot()
    appt = _held(db, fx, slot)
    create_payment_link(db, appt)
    db.refresh(appt)
    appt.hold_expires_at = now_ist().replace(tzinfo=None) - timedelta(minutes=1)
    db.commit()
    assert release_expired_holds(db) == 1
    assert is_slot_free(db, fx["doctor"].id, slot) is True
    booked = book_slot(db, fx["tenant"].id, fx["doctor"].id, fx["patient"].id,
                       slot, 15, booking_amount=0, payment_status="not_required")
    assert booked is not None and booked.status == "CONFIRMED"


# --- 4. timely cancel -> full refund ----------------------------------------

def test_timely_cancel_full_refund(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    result = apply_booking_outcome(db, appt.id, "timely_cancel")
    assert result["status"] == "cancelled"
    db.refresh(appt)
    assert appt.status == "CANCELLED" and appt.payment_status == "refunded"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "refunded"
    assert db.query(Notification).filter(
        Notification.template_name == "REFUND_UPDATE").count() >= 1


# --- 5. late cancel -> forfeit (default policy) ------------------------------

def test_late_cancel_forfeit(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _soon_slot(hours=1))  # within 4h cut-off
    result = apply_booking_outcome(db, appt.id, "late_cancel")
    assert result["status"] == "cancelled" and result["policy"] == "forfeit"
    db.refresh(appt)
    assert appt.status == "CANCELLED" and appt.payment_status == "forfeited"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "forfeited"


# --- 6. clinic-side cancel -> always full refund -----------------------------

def test_clinic_cancel_full_refund(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _soon_slot(hours=1))  # even inside cut-off
    result = apply_booking_outcome(db, appt.id, "clinic_cancel")
    assert result["status"] == "cancelled"
    db.refresh(appt)
    assert appt.status == "CANCELLED" and appt.payment_status == "refunded"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "refunded"


# --- 7. reschedule -> carry-forward, no re-pay --------------------------------

def test_reschedule_carry_forward(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    result = apply_booking_outcome(db, appt.id, "reschedule")
    assert result["status"] == "rescheduled"
    db.refresh(appt)
    assert appt.status == "RESCHEDULED" and appt.payment_status == "carry_forward"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "carry_forward"
    assert db.query(BookingPayment).filter(
        BookingPayment.appointment_id == appt.id).count() == 1  # no second payment


# --- 8. no-show forfeit (CONFIRMED + reminder sent) ---------------------------

def test_no_show_forfeit(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _past_slot(hours=2))
    # Without a reminder, no-show must be refused — never forfeit blindly.
    refused = apply_booking_outcome(db, appt.id, "no_show")
    assert refused["status"] == "error"
    db.refresh(appt)
    assert appt.status == "CONFIRMED" and appt.payment_status == "paid"
    _add_reminder(db, fx, appt)
    result = apply_booking_outcome(db, appt.id, "no_show")
    assert result["status"] == "no_show"
    db.refresh(appt)
    assert appt.status == "NO_SHOW" and appt.payment_status == "forfeited"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "forfeited"
    db.refresh(fx["patient"])
    assert fx["patient"].no_show_count == 1


# --- 9. reminder-cancel refund (never forfeit) ---------------------------------

def test_reminder_cancel_refund(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    _add_reminder(db, fx, appt)  # patient replied "nahi aa paunga" in time
    result = apply_booking_outcome(db, appt.id, "timely_cancel")
    assert result["status"] == "cancelled"
    db.refresh(appt)
    assert appt.payment_status == "refunded"  # refund, never forfeit
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "refunded"


# --- 10. no-UPI exception: manual confirm --------------------------------------

def test_no_upi_manual_confirm(fx):
    db = fx["db"]
    appt = _held(db, fx, _tomorrow_slot())
    result = manual_confirm(db, appt.id, by="reception")
    assert result["status"] == "confirmed" and result["manual"] is True
    db.refresh(appt)
    assert appt.status == "CONFIRMED" and appt.token_no is not None
    # Manual confirm never claims a payment was made.
    assert appt.payment_status == "not_required"
    assert db.query(BookingPayment).filter(
        BookingPayment.appointment_id == appt.id, BookingPayment.status == "paid").count() == 0


# --- 11. duplicate webhook: exactly one CONFIRMED / token / payment -----------

def test_duplicate_webhook(fx):
    db = fx["db"]
    appt = _held(db, fx, _tomorrow_slot())
    link = create_payment_link(db, appt)
    payload = {"gateway_payment_id": "gw-dup-1", "amount": 100,
               "status": "SUCCESS", "order_id": link["payment_id"]}
    first = handle_payment_webhook(db, payload)
    second = handle_payment_webhook(db, dict(payload))
    assert first["status"] == "paid"
    assert second["status"] == "already_processed"
    assert db.query(BookingPayment).filter(
        BookingPayment.appointment_id == appt.id).count() == 1
    db.refresh(appt)
    assert appt.status == "CONFIRMED" and appt.token_no == 1
    assert db.query(Appointment).filter(
        Appointment.doctor_id == fx["doctor"].id,
        Appointment.date == appt.date, Appointment.status == "CONFIRMED").count() == 1


# --- 12. wrong-amount webhook rejected ------------------------------------------

def test_wrong_amount_webhook_rejected(fx):
    db = fx["db"]
    appt = _held(db, fx, _tomorrow_slot())
    link = create_payment_link(db, appt)
    result = handle_payment_webhook(db, {
        "gateway_payment_id": "gw-wrong-1", "amount": 999,
        "status": "SUCCESS", "order_id": link["payment_id"],
    })
    assert result["status"] == "rejected" and result["reason"] == "amount_mismatch"
    db.refresh(appt)
    assert appt.status == "HELD_PENDING_PAYMENT" and appt.token_no is None
    payment = db.query(BookingPayment).filter(BookingPayment.id == link["payment_id"]).first()
    assert payment.status == "link_sent"  # not paid


# --- visit completed + refund + signature ---------------------------------------

def test_visit_completed_adjusts_amount(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    result = apply_booking_outcome(db, appt.id, "visit_completed")
    assert result["status"] == "completed"
    db.refresh(appt)
    assert appt.status == "COMPLETED" and appt.payment_status == "adjusted"
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    assert payment.status == "adjusted"


def test_initiate_refund_direct(fx):
    db = fx["db"]
    appt, payment_id = _paid(db, fx, _tomorrow_slot())
    result = initiate_refund(db, payment_id, "timely_cancel")
    assert result["status"] == "refunded"
    again = initiate_refund(db, payment_id, "timely_cancel")
    assert again["status"] == "refunded" and again.get("already") is True
    assert db.query(BookingPayment).filter(BookingPayment.id == payment_id).first().status == "refunded"


def test_verify_webhook_signature_simulated(fx):
    import json
    good = json.dumps({"test_signature": "valid", "amount": 100}).encode()
    bad = json.dumps({"amount": 100}).encode()
    assert verify_webhook_signature(good, "") is True
    assert verify_webhook_signature(bad, "") is False
    assert verify_webhook_signature(b"not json", "") is False


def test_create_payment_link_disabled_amount(fx):
    db = fx["db"]
    fx["clinic"].booking_amount_enabled = False
    db.commit()
    appt = _held(db, fx, _tomorrow_slot())
    result = create_payment_link(db, appt)
    assert result["amount"] == 0 and result["link"] is None
    assert result["status"] == "not_required"
