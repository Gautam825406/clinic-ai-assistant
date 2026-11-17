"""Slot engine — deterministic code only (PRD Sec 6 FR-04, Sec 11).

The LLM never decides whether a slot is free or a booking is saved. This
module + the DB UNIQUE(doctor_id, slot_start) constraint decide that.
All datetimes here are naive IST (Asia/Kolkata) wall-clock times, matching
how clinics think about their day; IST has no DST.
"""
from datetime import date, datetime, time, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .date_parser import now_ist
from .models import Appointment, Doctor, Holiday, Schedule

HOLD_SECONDS = 120  # PRD Sec 11.3: 120-sec Redis hold during a call


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def _is_holiday(db: Session, tenant_id: str, doctor_id: str, day: date) -> bool:
    q = db.query(Holiday).filter(Holiday.tenant_id == tenant_id, Holiday.date == day)
    return q.filter((Holiday.doctor_id.is_(None)) | (Holiday.doctor_id == doctor_id)).first() is not None


def generate_slots(db: Session, doctor_id: str, day: date, duration_minutes: int | None = None) -> list[datetime]:
    """All bookable slot starts for a doctor on a day, from their weekly
    schedule, minus breaks and holidays. Past slots today are excluded."""
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    if not doctor or not doctor.active:
        return []
    duration = duration_minutes or doctor.slot_minutes
    schedules = (
        db.query(Schedule)
        .filter(Schedule.doctor_id == doctor_id, Schedule.weekday == day.weekday())
        .all()
    )
    if not schedules or _is_holiday(db, doctor.tenant_id, doctor_id, day):
        return []
    slots: list[datetime] = []
    now = now_ist().replace(tzinfo=None)
    for sched in schedules:
        start = datetime.combine(day, _parse_hhmm(sched.start_time))
        end = datetime.combine(day, _parse_hhmm(sched.end_time))
        break_start = datetime.combine(day, _parse_hhmm(sched.break_start)) if sched.break_start else None
        break_end = datetime.combine(day, _parse_hhmm(sched.break_end)) if sched.break_end else None
        cur = start
        while cur + timedelta(minutes=duration) <= end:
            slot_end = cur + timedelta(minutes=duration)
            in_break = break_start and break_end and cur < break_end and slot_end > break_start
            if not in_break:
                if day != now.date() or cur > now + timedelta(minutes=15):
                    slots.append(cur)
            cur = slot_end
    # remove slots already taken (CONFIRMED or unexpired HELD)
    taken = {
        a.slot_start for a in db.query(Appointment).filter(
            Appointment.doctor_id == doctor_id,
            Appointment.date == day,
            Appointment.status.in_(("CONFIRMED", "HELD_PENDING_PAYMENT")),
        ).all()
        if a.status == "CONFIRMED" or (a.hold_expires_at and a.hold_expires_at > now)
    }
    return [s for s in slots if s not in taken]


def is_slot_free(db: Session, doctor_id: str, slot_start: datetime) -> bool:
    """True only if no live CONFIRMED / unexpired HELD appointment occupies it."""
    now = now_ist().replace(tzinfo=None)
    clash = db.query(Appointment).filter(
        Appointment.doctor_id == doctor_id,
        Appointment.slot_start == slot_start,
        Appointment.status.in_(("CONFIRMED", "HELD_PENDING_PAYMENT")),
    ).all()
    for a in clash:
        if a.status == "CONFIRMED":
            return False
        if a.hold_expires_at and a.hold_expires_at > now:
            return False
    return True


def release_expired_holds(db: Session) -> int:
    """Free HELD_PENDING_PAYMENT appointments whose hold expired. Returns count."""
    now = now_ist().replace(tzinfo=None)
    expired = db.query(Appointment).filter(
        Appointment.status == "HELD_PENDING_PAYMENT",
        Appointment.hold_expires_at.isnot(None),
        Appointment.hold_expires_at <= now,
    ).all()
    for a in expired:
        db.delete(a)
    if expired:
        db.commit()
    return len(expired)


def hold_slot(db: Session, tenant_id: str, doctor_id: str, patient_id: str,
              slot_start: datetime, duration_minutes: int, reason_line: str = "",
              appointment_type_id: str | None = None,
              hold_seconds: int = HOLD_SECONDS) -> Appointment | None:
    """120-sec hold (PRD 11.3). Returns None if the slot is not free.
    The caller extends hold_expires_at for the FR-26 payment hold."""
    release_expired_holds(db)
    if not is_slot_free(db, doctor_id, slot_start):
        return None
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    appt = Appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, patient_id=patient_id,
        appointment_type_id=appointment_type_id, date=slot_start.date(),
        slot_start=slot_start, slot_end=slot_end, reason_line=reason_line,
        status="HELD_PENDING_PAYMENT", source="voice",
        hold_expires_at=now_ist().replace(tzinfo=None) + timedelta(seconds=hold_seconds),
    )
    db.add(appt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(appt)
    return appt


def book_slot(db: Session, tenant_id: str, doctor_id: str, patient_id: str,
              slot_start: datetime, duration_minutes: int, reason_line: str = "",
              appointment_type_id: str | None = None, source: str = "voice",
              booking_amount: int = 0, payment_status: str = "not_required") -> Appointment | None:
    """Deterministic booking. Re-checks freeness inside the transaction;
    the DB unique constraint is the final guard — exactly one of N parallel
    attempts can win. Returns None when the slot is taken."""
    release_expired_holds(db)
    if not is_slot_free(db, doctor_id, slot_start):
        return None
    slot_end = slot_start + timedelta(minutes=duration_minutes)
    day_count = db.query(Appointment).filter(
        Appointment.doctor_id == doctor_id, Appointment.date == slot_start.date(),
        Appointment.status.in_(("CONFIRMED", "HELD_PENDING_PAYMENT")),
    ).count()
    appt = Appointment(
        tenant_id=tenant_id, doctor_id=doctor_id, patient_id=patient_id,
        appointment_type_id=appointment_type_id, date=slot_start.date(),
        slot_start=slot_start, slot_end=slot_end, reason_line=reason_line,
        status="CONFIRMED", source=source, token_no=day_count + 1,
        booking_amount=booking_amount, payment_status=payment_status,
    )
    db.add(appt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(appt)
    return appt


def find_alternatives(db: Session, doctor_id: str, wanted: datetime,
                      duration_minutes: int | None = None, n: int = 2) -> list[datetime]:
    """PRD FR-06: the 2 nearest free alternatives, same day first, then later days."""
    doctor = db.query(Doctor).filter(Doctor.id == doctor_id).first()
    duration = duration_minutes or (doctor.slot_minutes if doctor else 15)
    out: list[datetime] = []
    for offset in range(0, 7):
        day = wanted.date() + timedelta(days=offset)
        for slot in generate_slots(db, doctor_id, day, duration):
            if offset == 0 and slot <= wanted:
                continue
            out.append(slot)
            if len(out) >= n:
                return out
    return out
