"""Dialogue + safety tests — PRD Sec 7 / Sec 11, FR-26.

Pure-function tests plus DialogueEngine runs against the test DB set up
by backend/tests/conftest.py (fresh temp-file SQLite per pytest session;
see the note in conftest.py for why it is not :memory:).
"""
import json
import os

import pytest

from app.database import Base, SessionLocal, init_db
from app.date_parser import parse_datetime_hint
from app.dialogue import DialogueEngine, extract_fields
from app.models import Appointment, CallbackTicket, Patient
from app.safety import (
    detect_advice_request,
    detect_emergency,
    deflection_response,
    emergency_script,
)
from app.slot_engine import book_slot
from helpers import seed_clinic

EVALS = os.path.join(os.path.dirname(__file__), "..", "..", "evals")


def _load(name):
    with open(os.path.join(EVALS, name)) as f:
        return json.load(f)


@pytest.fixture()
def db():
    init_db()
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    yield session
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


def _drive(db, clinic, tenant_id, texts):
    """Run a list of caller turns through a fresh DialogueEngine session."""
    engine = DialogueEngine(db, tenant_id, clinic)
    session = {}
    out = []
    for text in texts:
        out.append(engine.process_text(session, text))
    return session, out


def _to_intent(db, clinic, tenant_id):
    """Drive greeting + consent; returns (session, engine, replies)."""
    engine = DialogueEngine(db, tenant_id, clinic)
    session = {}
    r1 = engine.process_text(session, "")
    r2 = engine.process_text(session, "haan")
    assert r1["state"] == "CONSENT"
    assert session.get("consent_line_played") is True
    assert "digital assistant hoon" in r1["reply"]
    assert "record" in r1["reply"]
    assert r2["state"] == "INTENT"
    return session, engine


# --------------------------------------------------------------------------
# Safety: advice deflection 20/20, emergency trigger 15/15
# --------------------------------------------------------------------------
def test_advice_20_deflect():
    cases = _load("advice_20.json")
    assert len(cases) == 20
    for case in cases:
        assert detect_advice_request(case["input"]), case
        reply = deflection_response()
        assert "doctor" in reply.lower()
        assert "dawa" not in reply.lower() or "nahi de sakta" in reply
        # Deflection never names a medicine, dose or diagnosis.
        for banned in ("paracetamol", "antibiotic", "mg", "diagnosis:"):
            assert banned not in reply.lower()


def test_advice_deflection_via_engine(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    for case in _load("advice_20.json"):
        out = engine.process_text(session, case["input"])
        assert out["action"] == "deflect_advice", case
        assert "doctor" in out["reply"].lower(), case
        assert out["state"] != "BOOK", case


def test_emergency_15_trigger():
    cases = _load("emergency_15.json")
    assert len(cases) == 15
    for case in cases:
        assert detect_emergency(case["input"]), case
        script = emergency_script()
        assert "112" in script
        assert "hospital" in script.lower()


def test_emergency_script_via_engine(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    for case in _load("emergency_15.json"):
        out = engine.process_text(session, case["input"])
        assert out["state"] == "EMERGENCY", case
        assert out["action"] == "emergency", case
        assert "112" in out["reply"], case
        assert session.get("emergency_alert") is True
        assert db.query(Appointment).filter(
            Appointment.tenant_id == tenant.id).count() == 0


def test_emergency_stops_booking_mid_flow(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    engine.process_text(
        session, "Mera naam Rahul Sharma hai, kal subah 10 baje appointment chahiye")
    out = engine.process_text(session, "Seene me dard ho raha hai!")
    assert out["state"] == "EMERGENCY"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id).count() == 0


# --------------------------------------------------------------------------
# Extraction
# --------------------------------------------------------------------------
def test_extract_fields_full_booking_confidence_09():
    ext = extract_fields(
        "Mera naam Rahul Sharma hai, mobile 9876543210, "
        "kal subah 10 baje appointment chahiye, mujhe bukhar hai.")
    assert ext.intent == "book"
    assert ext.patient_name == "Rahul Sharma"
    assert ext.mobile == "9876543210"
    assert ext.date_hint == "kal"
    assert ext.time_hint is not None and "10" in ext.time_hint
    assert ext.confidence == 0.9


def test_extract_fields_partial_confidence_below_09():
    ext = extract_fields("Kal appointment chahiye.")
    assert ext.intent == "book"
    assert ext.confidence == 0.75
    ext2 = extract_fields("Mera naam Priya Verma hai.")
    assert ext2.patient_name == "Priya Verma"
    assert 0.75 <= ext2.confidence < 0.9


def test_extract_fields_doctor_hint_and_reason_capped():
    ext = extract_fields("Dr. Sharma se milna hai, mujhe bahut tez bukhar aur sir dard hai kal subah 10 baje")
    assert ext.doctor_hint == "Sharma"
    assert ext.reason_line is not None
    assert len(ext.reason_line.split()) <= 8


def test_extract_fields_emergency_first():
    ext = extract_fields("Kal subah 10 baje appointment chahiye, saans nahi aa rahi!")
    assert ext.intent == "emergency"


# --------------------------------------------------------------------------
# Booking happy path + double-confirm
# --------------------------------------------------------------------------
def test_booking_happy_path_confirm_back_then_book(db):
    tenant, clinic, doctor = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Mera naam Rahul Sharma hai, mobile 9876543210, "
                 "kal subah 10 baje appointment chahiye")
    assert out["state"] == "CONFIRM_BACK", out
    assert out["action"] == "confirm_back"
    # Confirm-back reads name, mobile last-4, date with weekday, time, doctor.
    assert "Rahul Sharma" in out["reply"]
    assert "3210" in out["reply"]
    assert "Dr. Sharma" in out["reply"]
    assert "10" in out["reply"]
    # Double-confirm: no booking exists after the first turn.
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id).count() == 0

    out = engine.process_text(session, "haan")
    assert out["state"] == "BOOK", out
    assert out["action"] == "booked"
    assert out.get("appointment_id")
    appt = db.query(Appointment).filter(
        Appointment.id == out["appointment_id"]).first()
    assert appt is not None and appt.status == "CONFIRMED"
    assert appt.doctor_id == doctor.id
    assert appt.token_no == 1
    patient = db.query(Patient).filter(Patient.id == appt.patient_id).first()
    assert patient.mobile == "9876543210"
    assert patient.name == "Rahul Sharma"


def test_no_booking_on_first_turn_with_full_details(db):
    """Providing everything in one turn must stop at CONFIRM_BACK."""
    tenant, clinic, _ = seed_clinic(db)
    session, out = _drive(db, clinic, tenant.id, [
        "", "haan",
        "Mera naam Priya Verma hai, mobile 9812345678, parso shaam 5 baje appointment chahiye",
    ])
    assert out[-1]["state"] == "CONFIRM_BACK"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id).count() == 0


def test_collect_asks_one_question_at_a_time(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(session, "Appointment book karni hai.")
    assert out["state"] == "COLLECT"
    assert out["action"] == "ask_name"
    out = engine.process_text(session, "Rahul Sharma")
    assert out["action"] == "ask_mobile"
    out = engine.process_text(session, "9876543210")
    assert out["action"] == "ask_datetime"
    out = engine.process_text(session, "kal subah 10 baje")
    assert out["state"] == "CONFIRM_BACK"


# --------------------------------------------------------------------------
# FAQ: never hallucinate
# --------------------------------------------------------------------------
def test_faq_unconfigured_fee_goes_to_reception(db):
    tenant, clinic, _ = seed_clinic(db, fees_text="")
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(session, "Clinic ki fees kitni hai?")
    assert out["action"] == "faq"
    assert "reception se confirm" in out["reply"]
    assert "500" not in out["reply"]


def test_faq_configured_answers_come_from_config(db):
    tenant, clinic, _ = seed_clinic(
        db, fees_text="₹500", address="MG Road, Bangalore")
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(session, "Fees kitni hai?")
    assert "₹500" in out["reply"]
    out = engine.process_text(session, "Clinic kahan hai?")
    assert "MG Road, Bangalore" in out["reply"]
    out = engine.process_text(session, "Clinic kab khulta hai?")
    assert "09:00" in out["reply"] and "21:00" in out["reply"]


# --------------------------------------------------------------------------
# Human handoff after 2 failed understandings; noise never dead-ends
# --------------------------------------------------------------------------
def test_two_failed_attempts_human_handoff(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(session, "hmm kya bol rahe ho")
    assert out["state"] == "INTENT"
    assert out["action"] == "clarify"
    out = engine.process_text(session, "kuch samajh nahi aa raha")
    assert out["state"] == "HUMAN", out
    assert out["action"] == "human_handoff"
    assert "reception" in out["reply"].lower()
    ticket = db.query(CallbackTicket).filter(
        CallbackTicket.tenant_id == tenant.id).first()
    assert ticket is not None and ticket.status == "open"


def test_human_keyword_handoff(db):
    tenant, clinic, _ = seed_clinic(db)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(session, "Mujhe reception se baat karni hai.")
    assert out["state"] == "HUMAN"
    assert out["action"] == "human_handoff"


def test_noise_15_never_dead_ends(db):
    cases = _load("noise_15.json")
    assert len(cases) == 15
    for case in cases:
        tenant, clinic, _ = seed_clinic(db)
        session, engine = _to_intent(db, clinic, tenant.id)
        out = engine.process_text(session, case["input"])
        assert out["reply"], case
        assert out["state"] in ("INTENT", "COLLECT", "HUMAN"), case
        out = engine.process_text(session, case["input"])
        assert out["state"] in ("INTENT", "COLLECT", "HUMAN"), case
        assert out["reply"], case


# --------------------------------------------------------------------------
# Slot full -> exactly 2 alternatives
# --------------------------------------------------------------------------
def test_slot_full_offers_two_alternatives(db):
    tenant, clinic, doctor = seed_clinic(db)
    # Pre-book tomorrow 10:00 for another patient.
    other = Patient(tenant_id=tenant.id, name="Other", mobile="9000000001")
    db.add(other)
    db.commit()
    slot = parse_datetime_hint("kal subah 10 baje")
    assert book_slot(db, tenant.id, doctor.id, other.id, slot, 15) is not None

    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Mera naam Rahul Sharma hai, mobile 9876543210, "
                 "kal subah 10 baje appointment chahiye")
    assert out["state"] == "CHECK_SLOTS", out
    assert out["action"] == "slot_full"
    assert "1." in out["reply"] and "2." in out["reply"]
    assert "3." not in out["reply"]
    out = engine.process_text(session, "pehla wala")
    assert out["state"] == "CONFIRM_BACK", out
    out = engine.process_text(session, "haan")
    assert out["state"] == "BOOK"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.status == "CONFIRMED").count() == 2


# --------------------------------------------------------------------------
# Reschedule / cancel
# --------------------------------------------------------------------------
def _seed_confirmed(db, tenant, doctor, mobile="9876543210", when="kal subah 10 baje"):
    patient = Patient(tenant_id=tenant.id, name="Rahul Sharma", mobile=mobile)
    db.add(patient)
    db.commit()
    slot = parse_datetime_hint(when)
    appt = book_slot(db, tenant.id, doctor.id, patient.id, slot, 15)
    assert appt is not None
    return patient, appt


def test_reschedule_flow(db):
    tenant, clinic, doctor = seed_clinic(db)
    _, old = _seed_confirmed(db, tenant, doctor)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Meri appointment reschedule karni hai, mobile 9876543210.")
    assert out["state"] == "COLLECT", out
    out = engine.process_text(session, "parso subah 11 baje")
    assert out["state"] == "CONFIRM_BACK", out
    out = engine.process_text(session, "haan")
    assert out["state"] == "BOOK", out
    assert db.query(Appointment).filter(
        Appointment.id == old.id).first().status == "RESCHEDULED"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.status == "CONFIRMED").count() == 1


def test_cancel_flow(db):
    tenant, clinic, doctor = seed_clinic(db)
    _, old = _seed_confirmed(db, tenant, doctor)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Appointment cancel karni hai, mobile 9876543210.")
    assert out["state"] == "CONFIRM_BACK", out
    out = engine.process_text(session, "haan")
    assert out["action"] == "cancelled"
    assert db.query(Appointment).filter(
        Appointment.id == old.id).first().status == "CANCELLED"


def test_reschedule_unknown_mobile_no_booking(db):
    tenant, clinic, doctor = seed_clinic(db)
    _seed_confirmed(db, tenant, doctor)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Meri appointment reschedule karni hai, mobile 9999999999.")
    assert out["action"] == "lookup_failed"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.status == "CONFIRMED").count() == 1


# --------------------------------------------------------------------------
# FR-26: booking amount -> PAYMENT_PENDING, never confirmed on the call
# --------------------------------------------------------------------------
def test_booking_amount_goes_to_payment_pending(db):
    tenant, clinic, _ = seed_clinic(db, booking_amount_enabled=True)
    session, engine = _to_intent(db, clinic, tenant.id)
    out = engine.process_text(
        session, "Mera naam Rahul Sharma hai, mobile 9876543210, "
                 "kal subah 10 baje appointment chahiye")
    assert out["state"] == "CONFIRM_BACK"
    out = engine.process_text(session, "haan")
    assert out["state"] == "PAYMENT_PENDING", out
    assert out["action"] == "payment_pending"
    assert "₹100" in out["reply"]
    assert "adjust" in out["reply"]
    assert "refund" in out["reply"]
    assert "forfeit" in out["reply"]
    appt = db.query(Appointment).filter(
        Appointment.id == out["appointment_id"]).first()
    assert appt.status == "HELD_PENDING_PAYMENT"
    assert appt.payment_status == "pending"
    assert db.query(Appointment).filter(
        Appointment.tenant_id == tenant.id,
        Appointment.status == "CONFIRMED").count() == 0


# ---------------------------------------------------------------------------
# Regression tests — found by live smoke test, 18 Nov 2026 (integration bugs)
# ---------------------------------------------------------------------------
def test_first_turn_intent_survives_consent(db):
    """The first utterance already says the whole job ("kal shaam Dr. Sharma
    ko dikhana hai"). After the consent "Haan", the engine must NOT re-ask
    intent from scratch — a bare name must be collected as the name."""
    tenant, clinic, _ = seed_clinic(db)
    _, out = _drive(db, clinic, tenant.id, [
        "Namaste, mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai",
        "Haan",
        "Rahul Verma",
        "9000000001",
        "5 baje wala kar do",
    ])
    assert out[-1]["state"] == "CONFIRM_BACK", out[-1]
    assert "Rahul Verma" in out[-1]["reply"]
    assert "5:00" in out[-1]["reply"]
    assert "shaam" in out[-1]["reply"]


def test_period_memory_shaam_plus_bare_hour_is_5pm(db):
    """'kal shaam' (turn 1) + '5 baje' (turn 5) must resolve to 17:00, not 05:00."""
    from datetime import timedelta

    from app.date_parser import now_ist
    tenant, clinic, doctor = seed_clinic(db)
    _, out = _drive(db, clinic, tenant.id, [
        "Namaste, mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai",
        "Haan",
        "Rahul Verma",
        "9000000001",
        "5 baje wala kar do",
        "Haan sahi hai",
    ])
    assert out[-1]["state"] == "BOOK", out[-1]
    appt = db.query(Appointment).filter(
        Appointment.id == out[-1]["appointment_id"]).first()
    assert appt is not None and appt.status == "CONFIRMED"
    assert appt.slot_start.hour == 17, appt.slot_start
    assert appt.slot_start.date() == (now_ist().date() + timedelta(days=1))


def test_haan_after_alternatives_picks_first(db):
    """Slot full -> 2 alternatives offered -> 'Haan sahi hai' picks the first."""
    from datetime import datetime, timedelta

    from app.date_parser import now_ist
    from app.slot_engine import book_slot
    tenant, clinic, doctor = seed_clinic(db)
    patient = Patient(tenant_id=tenant.id, name="Pehla Patient", mobile="9000000009")
    db.add(patient)
    db.commit()
    tomorrow = now_ist().date() + timedelta(days=1)
    wanted = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 17, 0)
    assert book_slot(db, tenant.id, doctor.id, patient.id, wanted, 15) is not None
    _, out = _drive(db, clinic, tenant.id, [
        "",
        "Haan",
        "Mera naam Amit hai, mobile 9811111111, kal shaam 5 baje appointment chahiye",
    ])
    assert out[-1]["state"] == "CHECK_SLOTS", out[-1]
    session = {}
    engine = DialogueEngine(db, tenant.id, clinic)
    for text in ["", "Haan",
                 "Mera naam Amit hai, mobile 9811111111, kal shaam 5 baje appointment chahiye",
                 "Haan sahi hai"]:
        last = engine.process_text(session, text)
    assert last["state"] == "CONFIRM_BACK", last
    assert "5:15" in last["reply"] or "5:30" in last["reply"], last["reply"]
