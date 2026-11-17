"""Booking router — slots, manual booking (FR-20), cancel, reschedule,
outcome (FR-26 via payments.apply_booking_outcome when that module exists).

Manual bookings made by reception are CONFIRMED at entry (reception has
taken/confirmed payment or exempted the patient — the no-UPI exception in
FR-26); a booking amount still due shows as payment_status "pending" and
is adjusted against the visit fee. Voice bookings, by contrast, stay
HELD_PENDING_PAYMENT until the payment webhook confirms them.
"""
import datetime as dt
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AppointmentType, Patient, User
from ..schemas import AppointmentCreate
from ..security import get_current_user
from ..slot_engine import book_slot, generate_slots, is_slot_free
from .common import appointment_dict, appointment_for, doctor_for

router = APIRouter(tags=["booking"])


class OutcomeRequest(BaseModel):
    outcome: str  # visit_completed | timely_cancel | late_cancel | clinic_cancel | no_show | reschedule


class RescheduleRequest(BaseModel):
    slot_start: datetime
    date: dt.date | None = None


def _booking_amount(db: Session, tenant_id: str, appointment_type_id: str | None) -> int:
    """FR-26 amount: type override wins, else clinic default, else 0 when OFF."""
    from .common import clinic_for

    clinic = clinic_for(db, tenant_id)
    if not clinic.booking_amount_enabled:
        return 0
    if appointment_type_id:
        atype = db.query(AppointmentType).filter(
            AppointmentType.id == appointment_type_id,
            AppointmentType.tenant_id == tenant_id).first()
        if atype is not None and atype.booking_amount_override is not None:
            return atype.booking_amount_override
    return clinic.booking_amount_default or 0


def _get_or_create_patient(db: Session, tenant_id: str, name: str, mobile: str) -> Patient:
    patient = db.query(Patient).filter(
        Patient.tenant_id == tenant_id, Patient.mobile == mobile).first()
    if patient is None:
        patient = Patient(tenant_id=tenant_id, name=name, mobile=mobile)
        db.add(patient)
        db.commit()
        db.refresh(patient)
    return patient


@router.get("/slots")
def list_slots(doctor_id: str, date: date, appointment_type_id: str | None = None,
               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    doctor = doctor_for(db, user.tenant_id, doctor_id)
    duration = doctor.slot_minutes
    if appointment_type_id:
        atype = db.query(AppointmentType).filter(
            AppointmentType.id == appointment_type_id,
            AppointmentType.tenant_id == user.tenant_id).first()
        if atype is None:
            raise HTTPException(status_code=404, detail="Appointment type not found")
        duration = atype.duration_minutes
    slots = generate_slots(db, doctor_id, date, duration)
    return {"doctor_id": doctor_id, "date": date.isoformat(),
            "duration_minutes": duration,
            "slots": [s.isoformat() for s in slots]}


@router.post("/appointments", status_code=201)
def create_appointment(body: AppointmentCreate, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    doctor = doctor_for(db, user.tenant_id, body.doctor_id)
    duration = doctor.slot_minutes
    if body.appointment_type_id:
        atype = db.query(AppointmentType).filter(
            AppointmentType.id == body.appointment_type_id,
            AppointmentType.tenant_id == user.tenant_id).first()
        if atype is None:
            raise HTTPException(status_code=404, detail="Appointment type not found")
        duration = atype.duration_minutes
    if body.slot_start.date() != body.date:
        raise HTTPException(status_code=400, detail="slot_start date must match date")
    patient = _get_or_create_patient(db, user.tenant_id, body.patient_name, body.patient_mobile)
    amount = _booking_amount(db, user.tenant_id, body.appointment_type_id)
    appt = book_slot(
        db, user.tenant_id, body.doctor_id, patient.id, body.slot_start, duration,
        reason_line=body.reason_line, appointment_type_id=body.appointment_type_id,
        source=body.source or "manual", booking_amount=amount,
        payment_status="pending" if amount > 0 else "not_required",
    )
    if appt is None:
        raise HTTPException(status_code=409, detail="Ye slot pehle se booked hai — koi aur slot chunein")
    return appointment_dict(db, appt)


@router.get("/appointments/{appointment_id}")
def get_appointment(appointment_id: str, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    return appointment_dict(db, appointment_for(db, user.tenant_id, appointment_id))


@router.post("/appointments/{appointment_id}/cancel")
def cancel_appointment(appointment_id: str, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    appt = appointment_for(db, user.tenant_id, appointment_id)
    if appt.status in ("CANCELLED", "COMPLETED", "NO_SHOW"):
        raise HTTPException(status_code=400, detail=f"Appointment already {appt.status}")
    try:
        from ..payments import apply_booking_outcome
    except Exception:
        apply_booking_outcome = None
    if apply_booking_outcome is not None:
        from ..date_parser import now_ist
        from .common import clinic_for

        clinic = clinic_for(db, user.tenant_id)
        hours_left = (appt.slot_start - now_ist().replace(tzinfo=None)).total_seconds() / 3600
        outcome = "timely_cancel" if hours_left >= (clinic.timely_cancel_hours or 4) else "late_cancel"
        result = apply_booking_outcome(db, appt.id, outcome)
        db.refresh(appt)
        return {**appointment_dict(db, appt), "outcome": outcome, "payment": result}
    appt.status = "CANCELLED"
    db.commit()
    return appointment_dict(db, appt)


@router.post("/appointments/{appointment_id}/reschedule")
def reschedule_appointment(appointment_id: str, body: RescheduleRequest,
                           user: User = Depends(get_current_user),
                           db: Session = Depends(get_db)):
    appt = appointment_for(db, user.tenant_id, appointment_id)
    if appt.status in ("CANCELLED", "COMPLETED", "NO_SHOW"):
        raise HTTPException(status_code=400, detail=f"Appointment already {appt.status}")
    new_date = body.date or body.slot_start.date()
    if body.slot_start.date() != new_date:
        raise HTTPException(status_code=400, detail="slot_start date must match date")
    if not is_slot_free(db, appt.doctor_id, body.slot_start):
        raise HTTPException(status_code=409, detail="Ye slot pehle se booked hai — koi aur slot chunein")
    from datetime import timedelta

    duration = (appt.slot_end - appt.slot_start).total_seconds() / 60
    appt.slot_start = body.slot_start
    appt.slot_end = body.slot_start + timedelta(minutes=int(duration))
    appt.date = new_date
    db.commit()
    db.refresh(appt)
    return appointment_dict(db, appt)


@router.post("/appointments/{appointment_id}/outcome")
def appointment_outcome(appointment_id: str, body: OutcomeRequest,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    appt = appointment_for(db, user.tenant_id, appointment_id)
    valid = ("visit_completed", "timely_cancel", "late_cancel",
             "clinic_cancel", "no_show", "reschedule")
    if body.outcome not in valid:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {valid}")
    if body.outcome == "reschedule":
        raise HTTPException(status_code=400,
                            detail="Reschedule ke liye /reschedule endpoint use karein")
    try:
        from ..payments import apply_booking_outcome
    except Exception:
        apply_booking_outcome = None
    if apply_booking_outcome is not None:
        result = apply_booking_outcome(db, appt.id, body.outcome)
        db.refresh(appt)
        return {**appointment_dict(db, appt), "payment": result}
    # Fallback until the payments module lands (CONTRACT: 503-style degradation
    # is for sibling-module calls; here the status change itself is safe to do).
    mapping = {"visit_completed": "COMPLETED", "timely_cancel": "CANCELLED",
               "late_cancel": "CANCELLED", "clinic_cancel": "CANCELLED",
               "no_show": "NO_SHOW"}
    appt.status = mapping[body.outcome]
    if body.outcome == "visit_completed":
        appt.payment_status = "adjusted"
    elif body.outcome == "timely_cancel":
        appt.payment_status = "refund_initiated"
    elif body.outcome in ("late_cancel", "no_show"):
        appt.payment_status = "forfeited"
    elif body.outcome == "clinic_cancel":
        appt.payment_status = "refund_initiated"
    if body.outcome == "no_show":
        patient = db.query(Patient).filter(Patient.id == appt.patient_id).first()
        if patient is not None:
            patient.no_show_count = (patient.no_show_count or 0) + 1
    db.commit()
    db.refresh(appt)
    return appointment_dict(db, appt)
