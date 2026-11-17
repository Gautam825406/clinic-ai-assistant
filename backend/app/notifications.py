"""Notifications — PRD FR-13 (daily digest), FR-14 (reminders),
FR-11/FR-12 delivery via WhatsApp, and no-show marking (FR-26).

Digest: time-ordered list, token, reason, callbacks-pending count,
per Appendix A Template 3 (DAILY_DIGEST).
"""
import logging
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from .date_parser import format_confirm_date, now_ist
from .models import (
    Appointment, CallbackTicket, Clinic, Doctor, Notification, Patient,
)
from .whatsapp import get_whatsapp, render_template

logger = logging.getLogger(__name__)


def _as_date(day) -> date:
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    if isinstance(day, str):
        return date.fromisoformat(day[:10])
    raise ValueError(f"day must be a date/datetime, got {day!r}")


def _clinic(db: Session, tenant_id: str) -> Clinic | None:
    return db.query(Clinic).filter(Clinic.tenant_id == tenant_id).first()


def _fmt_time(dt: datetime | None) -> str:
    return dt.strftime("%I:%M %p").lstrip("0") if dt else ""


def build_daily_digest(db: Session, tenant_id: str, day) -> str:
    """FR-13 digest text: today's CONFIRMED appointments in time order
    (time — patient — reason, Token N), total count, callbacks pending."""
    day_date = _as_date(day)
    clinic = _clinic(db, tenant_id)
    clinic_name = clinic.display_name if clinic else "Clinic"
    appointments = (db.query(Appointment)
                    .filter(Appointment.tenant_id == tenant_id,
                            Appointment.date == day_date,
                            Appointment.status == "CONFIRMED")
                    .order_by(Appointment.slot_start)
                    .all())
    entries = []
    for appt in appointments:
        patient = db.query(Patient).filter(
            Patient.id == appt.patient_id, Patient.tenant_id == tenant_id).first()
        entries.append({
            "time": _fmt_time(appt.slot_start),
            "patient_name": patient.name if patient else "",
            "reason_line": appt.reason_line or "",
            "token_no": appt.token_no,
        })
    callback_count = db.query(CallbackTicket).filter(
        CallbackTicket.tenant_id == tenant_id, CallbackTicket.status == "open").count()
    return render_template("DAILY_DIGEST", {
        "clinic_name": clinic_name,
        "date": format_confirm_date(datetime.combine(day_date, datetime.min.time())),
        "entries": entries,
        "total_count": len(entries),
        "callback_count": callback_count,
    })


def send_daily_digest(db: Session, tenant_id: str, day) -> dict:
    """FR-13: send the digest via WhatsApp to the clinic's doctor number
    and reception number (deduped). Returns the send summary."""
    digest = build_daily_digest(db, tenant_id, day)
    clinic = _clinic(db, tenant_id)
    recipients = []
    if clinic:
        for number in (clinic.whatsapp_doctor, clinic.whatsapp_reception):
            if number and number not in recipients:
                recipients.append(number)
    if not recipients:
        logger.warning("Daily digest skipped: no clinic WhatsApp numbers tenant=%s", tenant_id)
        return {"status": "no_recipients", "recipients": [], "digest": digest}
    provider = get_whatsapp(db, tenant_id)
    msg_ids = []
    for to in recipients:
        result = provider.send(to, "DAILY_DIGEST", digest, notification_type="daily_digest")
        if result.get("provider_msg_id"):
            msg_ids.append(result["provider_msg_id"])
    return {"status": "sent", "recipients": recipients,
            "provider_msg_ids": msg_ids, "digest": digest}


def send_reminders(db: Session, tenant_id: str) -> list:
    """FR-14: reminders for CONFIRMED appointments starting within
    clinic.reminder_hours_before (default 2h). Each reminder is a
    PATIENT_CONFIRMATION-style message recorded as a Notification of
    type "reminder" — that record is what the FR-26 no-show rule checks.
    Already-reminded patients (a reminder Notification today) are skipped.
    Returns the list of sends."""
    clinic = _clinic(db, tenant_id)
    hours = clinic.reminder_hours_before if clinic else 2
    now = now_ist().replace(tzinfo=None)
    window_end = now + timedelta(hours=hours or 2)
    appointments = (db.query(Appointment)
                    .filter(Appointment.tenant_id == tenant_id,
                            Appointment.status == "CONFIRMED",
                            Appointment.slot_start > now,
                            Appointment.slot_start <= window_end)
                    .order_by(Appointment.slot_start)
                    .all())
    provider = get_whatsapp(db, tenant_id)
    sent = []
    for appt in appointments:
        patient = db.query(Patient).filter(
            Patient.id == appt.patient_id, Patient.tenant_id == tenant_id).first()
        doctor = db.query(Doctor).filter(
            Doctor.id == appt.doctor_id, Doctor.tenant_id == tenant_id).first()
        if not patient or not patient.mobile:
            continue
        # FR-14 opt-out: patient ne kabhi "reminder band" / STOP bola ho to
        # dobara reminder mat bhejo (consent_flags.reminder_opt_out).
        if (patient.consent_flags or {}).get("reminder_opt_out"):
            continue
        already = db.query(Notification).filter(
            Notification.tenant_id == tenant_id,
            Notification.type == "reminder",
            Notification.to_number == patient.mobile,
            Notification.sent_at.isnot(None),
        ).all()
        if any(n.sent_at and n.sent_at.date() == now.date() for n in already):
            continue
        body = render_template("PATIENT_CONFIRMATION", {
            "clinic_name": clinic.display_name if clinic else "Clinic",
            "patient_name": patient.name,
            "doctor_name": doctor.name if doctor else "",
            "date": format_confirm_date(appt.slot_start),
            "time": _fmt_time(appt.slot_start),
            "token_no": appt.token_no if appt.token_no is not None else "",
        })
        result = provider.send(patient.mobile, "PATIENT_CONFIRMATION", body,
                               notification_type="reminder")
        sent.append({**result, "appointment_id": appt.id})
    return sent


def mark_no_show(db: Session, appointment_id, by: str = "reception") -> dict:
    """FR-26 no-show: only after the slot end + grace minutes have passed.
    Delegates to payments.apply_booking_outcome("no_show"), which itself
    refuses unless the booking was CONFIRMED and a reminder was sent."""
    from .models import Appointment as _Appointment
    appt = db.query(_Appointment).filter(_Appointment.id == appointment_id).first()
    if appt is None:
        return {"status": "error", "reason": "appointment_not_found",
                "appointment_id": str(appointment_id)}
    clinic = _clinic(db, appt.tenant_id)
    grace = clinic.no_show_grace_minutes if clinic else 15
    now = now_ist().replace(tzinfo=None)
    if now <= appt.slot_end + timedelta(minutes=grace or 15):
        return {"status": "error", "reason": "grace_not_passed",
                "appointment_id": appt.id, "by": by,
                "slot_end": appt.slot_end.isoformat(),
                "grace_minutes": grace}
    from .payments import apply_booking_outcome
    result = apply_booking_outcome(db, appt.id, "no_show")
    result["by"] = by
    return result
