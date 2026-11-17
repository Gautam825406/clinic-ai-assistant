"""Clinic router — FR-17 config panel, today list, doctors, schedules,
holidays, calls, callbacks, dashboard stats. All routes are tenant-scoped:
the clinic is always the caller's own; there is no tenant id in the URL.
"""
import datetime as dt
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..date_parser import now_ist
from ..models import (
    Appointment, Call, CallbackTicket, Doctor, Holiday, Schedule, User,
)
from ..schemas import ClinicConfigUpdate, DoctorCreate, ScheduleCreate
from ..security import get_current_user
from ..specialty_templates import TEMPLATES, apply_template_to_tenant
from .common import appointment_dict, clinic_dict, clinic_for, doctor_dict, doctor_for

router = APIRouter(tags=["clinics"])


class HolidayCreate(BaseModel):
    doctor_id: str | None = None
    date: dt.date
    reason: str = ""


class CallbackUpdate(BaseModel):
    status: str  # open | done


def _me(db: Session, user: User):
    return clinic_for(db, user.tenant_id)


@router.get("/clinics/me")
def get_my_clinic(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    clinic = _me(db, user)
    doctors = db.query(Doctor).filter(Doctor.tenant_id == user.tenant_id).all()
    return {**clinic_dict(clinic), "doctors": [doctor_dict(d) for d in doctors]}


@router.patch("/clinics/me")
def update_my_clinic(body: ClinicConfigUpdate, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    clinic = _me(db, user)
    data = body.model_dump(exclude_unset=True)
    new_template = data.pop("specialty_template_id", None)
    for key, value in data.items():
        if hasattr(clinic, key):
            setattr(clinic, key, value)
    if new_template is not None:
        if new_template not in TEMPLATES:
            raise HTTPException(status_code=400, detail=f"Unknown specialty template: {new_template}")
        clinic.specialty_template_id = new_template
    db.commit()
    db.refresh(clinic)
    if new_template is not None:
        apply_template_to_tenant(db, user.tenant_id, new_template)
    return clinic_dict(clinic)


@router.get("/clinics/me/today")
def today_list(day: date | None = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    day = day or now_ist().date()
    appts = (
        db.query(Appointment)
        .filter(Appointment.tenant_id == user.tenant_id, Appointment.date == day)
        .order_by(Appointment.slot_start)
        .all()
    )
    return {"date": day.isoformat(), "count": len(appts),
            "appointments": [appointment_dict(db, a) for a in appts]}


@router.post("/clinics/me/doctors", status_code=201)
def create_doctor(body: DoctorCreate, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    doctor = Doctor(tenant_id=user.tenant_id, name=body.name,
                    speciality=body.speciality, slot_minutes=body.slot_minutes)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor_dict(doctor)


@router.post("/clinics/me/schedules", status_code=201)
def create_schedule(body: ScheduleCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doctor_for(db, user.tenant_id, body.doctor_id)
    sched = Schedule(tenant_id=user.tenant_id, doctor_id=body.doctor_id,
                     weekday=body.weekday, start_time=body.start_time,
                     end_time=body.end_time, break_start=body.break_start,
                     break_end=body.break_end)
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return {"id": sched.id, "doctor_id": sched.doctor_id, "weekday": sched.weekday,
            "start_time": sched.start_time, "end_time": sched.end_time,
            "break_start": sched.break_start, "break_end": sched.break_end}


@router.post("/clinics/me/holidays", status_code=201)
def create_holiday(body: HolidayCreate, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    if body.doctor_id:
        doctor_for(db, user.tenant_id, body.doctor_id)
    holiday = Holiday(tenant_id=user.tenant_id, doctor_id=body.doctor_id,
                      date=body.date, reason=body.reason)
    db.add(holiday)
    db.commit()
    db.refresh(holiday)
    return {"id": holiday.id, "doctor_id": holiday.doctor_id,
            "date": holiday.date.isoformat(), "reason": holiday.reason}


@router.get("/clinics/me/calls")
def list_calls(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    calls = (
        db.query(Call).filter(Call.tenant_id == user.tenant_id)
        .order_by(Call.started_at.desc()).limit(200).all()
    )
    return {"count": len(calls), "calls": [
        {"id": c.id, "call_sid": c.call_sid, "direction": c.direction,
         "from_number": c.from_number,
         "started_at": c.started_at.isoformat() if c.started_at else None,
         "duration_sec": c.duration_sec, "outcome": c.outcome,
         "language_detected": c.language_detected,
         "failure_reason": c.failure_reason, "transcript": c.transcript or []}
        for c in calls]}


@router.get("/clinics/me/callbacks")
def list_callbacks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tickets = (
        db.query(CallbackTicket).filter(CallbackTicket.tenant_id == user.tenant_id)
        .order_by(CallbackTicket.created_at.desc()).all()
    )
    return {"count": len(tickets), "callbacks": [
        {"id": t.id, "name": t.name, "mobile": t.mobile, "reason": t.reason,
         "priority": t.priority, "status": t.status,
         "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in tickets]}


@router.patch("/clinics/me/callbacks/{ticket_id}")
def update_callback(ticket_id: str, body: CallbackUpdate,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ticket = db.query(CallbackTicket).filter(
        CallbackTicket.id == ticket_id, CallbackTicket.tenant_id == user.tenant_id).first()
    if ticket is None:
        raise HTTPException(status_code=404, detail="Callback not found")
    if body.status not in ("open", "done"):
        raise HTTPException(status_code=400, detail="status must be open or done")
    ticket.status = body.status
    db.commit()
    return {"id": ticket.id, "status": ticket.status}


@router.get("/clinics/me/dashboard-stats")
def dashboard_stats(day: date | None = None, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    day = day or now_ist().date()
    tid = user.tenant_id
    today_q = db.query(Appointment).filter(Appointment.tenant_id == tid, Appointment.date == day)
    paid_confirmed = today_q.filter(Appointment.status == "CONFIRMED").count()
    visits = today_q.filter(Appointment.status == "COMPLETED").count()
    no_shows = today_q.filter(Appointment.status == "NO_SHOW").count()
    forfeited = sum(
        a.booking_amount for a in today_q.filter(Appointment.payment_status == "forfeited").all())
    refunded = sum(
        a.booking_amount for a in today_q.filter(Appointment.payment_status.in_(("refund_initiated", "refunded"))).all())
    open_callbacks = db.query(CallbackTicket).filter(
        CallbackTicket.tenant_id == tid, CallbackTicket.status == "open").count()
    calls_today = db.query(Call).filter(Call.tenant_id == tid).count()
    return {
        "date": day.isoformat(),
        "today_total": today_q.count(),
        "paid_confirmed": paid_confirmed, "visits": visits, "no_shows": no_shows,
        "forfeited_rs": forfeited, "refunded_rs": refunded,
        "open_callbacks": open_callbacks, "calls_total": calls_today,
    }
