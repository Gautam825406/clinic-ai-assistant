"""Clinic router — FR-17 config panel, today list, doctors, schedules,
holidays, calls, callbacks, dashboard stats. All routes are tenant-scoped:
the clinic is always the caller's own; there is no tenant id in the URL.
"""
import csv
import datetime as dt
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..date_parser import now_ist
from ..models import (
    Appointment, Call, CallbackTicket, Doctor, Holiday, Patient, Schedule,
    Subscription, User,
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
    from ..slot_engine import ensure_default_schedule

    doctor = Doctor(tenant_id=user.tenant_id, name=body.name,
                    speciality=body.speciality, slot_minutes=body.slot_minutes)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    # v1.3.2: a doctor without a weekly schedule can never be booked
    # (live test: 0 slots for 19/20 Nov). Auto-create the General default
    # hours — Mon–Sat 09:00–13:00 + 16:00–21:00 — so slots exist from day 1.
    schedules_created = ensure_default_schedule(db, doctor)
    return {**doctor_dict(doctor), "schedules_created": schedules_created,
            "default_schedule": "Mon–Sat 09:00–13:00, 16:00–21:00"}


@router.post("/clinics/me/schedules", status_code=201)
def create_schedule(body: ScheduleCreate, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    doctor_for(db, user.tenant_id, body.doctor_id)
    # v1.3.2: identical schedule rows are idempotent — adding the same
    # hours twice used to duplicate every slot in the list.
    existing = db.query(Schedule).filter(
        Schedule.tenant_id == user.tenant_id, Schedule.doctor_id == body.doctor_id,
        Schedule.weekday == body.weekday, Schedule.start_time == body.start_time,
        Schedule.end_time == body.end_time,
        Schedule.break_start == body.break_start,
        Schedule.break_end == body.break_end).first()
    if existing is not None:
        return {"id": existing.id, "doctor_id": existing.doctor_id, "weekday": existing.weekday,
                "start_time": existing.start_time, "end_time": existing.end_time,
                "break_start": existing.break_start, "break_end": existing.break_end,
                "duplicate": True}
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


@router.get("/clinics/me/subscription")
def my_subscription(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """FR-23 (config-only hook): clinic ka plan + usage, bina fake billing.

    Subscription row (seeded at signup: plan 'pilot', status 'trial')
    aur PLANS (backend/app/plans.py — PRD Sec 13.2 indicative bands,
    price_status 'hypothesis') se plan detail. Koi payment, autopay
    mandate ya invoice yahan create nahi hota — wo future step hai
    (Cashfree prod keys + pilot per-call cost validation ke baad).
    """
    from ..plans import PLANS, get_plan

    sub = db.query(Subscription).filter(
        Subscription.tenant_id == user.tenant_id).first()
    plan_id = (sub.plan if sub else None) or "pilot"
    plan = get_plan(plan_id)
    return {
        "plan": plan,
        "status": sub.status if sub else "trial",
        "current_period_start": sub.current_period_start.isoformat() if sub and sub.current_period_start else None,
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        "autopay_mandate_id": sub.autopay_mandate_id if sub else None,
        "usage": {
            "calls_total": db.query(Call).filter(Call.tenant_id == user.tenant_id).count(),
            "appointments_total": db.query(Appointment).filter(Appointment.tenant_id == user.tenant_id).count(),
            "doctors": db.query(Doctor).filter(Doctor.tenant_id == user.tenant_id).count(),
        },
        "all_plans": [get_plan(pid) for pid in ("pilot", "starter", "growth", "clinic_plus")],
        "billing_note": "Prices are PRD Sec 13.2 indicative bands (hypothesis) — "
                        "final price pilot ke pehle 7 din ke asal per-call cost se "
                        "validate hone ke baad lock hoga. Abhi koi payment collect nahi hota.",
    }


@router.post("/clinics/me/reminders/send")
def send_reminders_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """FR-14 trigger: clinic ke reminder window mein aane wale CONFIRMED
    appointments ko WhatsApp reminder bhejo. Opt-out wale patients skip
    hote hain (notifications.send_reminders). Scheduler / cron is
    endpoint ko clinic khulne se pehle call karega; manual trigger bhi
    yahi hai."""
    from ..notifications import send_reminders

    sent = send_reminders(db, user.tenant_id)
    return {"status": "ok", "sent_count": len(sent), "sent": sent}


@router.post("/clinics/me/digest/send")
def send_digest_now(day: date | None = None, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """FR-13 trigger: aaj ki list ka daily digest doctor + reception ke
    WhatsApp par bhejo. Roz clinic khulne se 30 min pehle scheduler /
    cron is endpoint ko call karega."""
    from ..notifications import send_daily_digest

    day = day or now_ist().date()
    result = send_daily_digest(db, user.tenant_id, day)
    return {"date": day.isoformat(), **result}


@router.get("/clinics/me/export")
def export_my_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """PRD Sec 10.3: clinic apna data (appointments + patients) CSV mein
    export kar sakti hai — sirf apna tenant, bina support ko bole."""
    appts = (db.query(Appointment).filter(Appointment.tenant_id == user.tenant_id)
             .order_by(Appointment.slot_start).all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "slot_start", "slot_end", "patient_name", "patient_mobile",
                "doctor_id", "reason_line", "status", "source", "token_no",
                "payment_status", "booking_amount"])
    for a in appts:
        patient = db.query(Patient).filter(
            Patient.id == a.patient_id, Patient.tenant_id == user.tenant_id).first()
        w.writerow([a.date.isoformat() if a.date else "",
                    a.slot_start.isoformat() if a.slot_start else "",
                    a.slot_end.isoformat() if a.slot_end else "",
                    patient.name if patient else "",
                    patient.mobile if patient else "",
                    a.doctor_id, a.reason_line or "", a.status, a.source,
                    a.token_no if a.token_no is not None else "",
                    a.payment_status, a.booking_amount])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=clinic-ai-assistant-appointments.csv"})
