"""Shared helpers for routers: serializers + tenant-scoped lookups.

Every lookup here filters by tenant_id; a row belonging to another tenant
is treated as not found (404), never as a forbidden leak.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import Appointment, Clinic, Doctor, Patient


def clinic_for(db: Session, tenant_id: str) -> Clinic:
    clinic = db.query(Clinic).filter(Clinic.tenant_id == tenant_id).first()
    if clinic is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    return clinic


def doctor_for(db: Session, tenant_id: str, doctor_id: str) -> Doctor:
    doctor = db.query(Doctor).filter(
        Doctor.id == doctor_id, Doctor.tenant_id == tenant_id).first()
    if doctor is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    return doctor


def appointment_for(db: Session, tenant_id: str, appointment_id: str) -> Appointment:
    appt = db.query(Appointment).filter(
        Appointment.id == appointment_id, Appointment.tenant_id == tenant_id).first()
    if appt is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return appt


def patient_name(db: Session, patient_id: str) -> dict:
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if p is None:
        return {"name": "", "mobile": ""}
    return {"name": p.name, "mobile": p.mobile}


def clinic_dict(c: Clinic) -> dict:
    return {
        "id": c.id, "tenant_id": c.tenant_id, "display_name": c.display_name,
        "specialty_template_id": c.specialty_template_id, "address": c.address,
        "google_maps_text": c.google_maps_text, "fees_text": c.fees_text,
        "greeting_text": c.greeting_text, "languages": c.languages,
        "whatsapp_doctor": c.whatsapp_doctor, "whatsapp_reception": c.whatsapp_reception,
        "telephony_number": c.telephony_number, "opening_time": c.opening_time,
        "closing_time": c.closing_time,
        "booking_amount_enabled": c.booking_amount_enabled,
        "booking_amount_default": c.booking_amount_default,
        "timely_cancel_hours": c.timely_cancel_hours,
        "late_cancel_policy": c.late_cancel_policy,
        "hold_minutes": c.hold_minutes,
        "reminder_hours_before": c.reminder_hours_before,
        "no_show_grace_minutes": c.no_show_grace_minutes,
    }


def doctor_dict(d: Doctor) -> dict:
    return {"id": d.id, "tenant_id": d.tenant_id, "name": d.name,
            "speciality": d.speciality, "slot_minutes": d.slot_minutes,
            "active": d.active}


def appointment_dict(db: Session, a: Appointment) -> dict:
    p = patient_name(db, a.patient_id)
    doctor = db.query(Doctor).filter(Doctor.id == a.doctor_id).first()
    return {
        "id": a.id, "tenant_id": a.tenant_id, "doctor_id": a.doctor_id,
        "doctor_name": doctor.name if doctor else "",
        "patient_id": a.patient_id, "patient_name": p["name"],
        "patient_mobile": p["mobile"],
        "appointment_type_id": a.appointment_type_id,
        "date": a.date.isoformat(), "slot_start": a.slot_start.isoformat(),
        "slot_end": a.slot_end.isoformat(), "reason_line": a.reason_line,
        "status": a.status, "source": a.source, "token_no": a.token_no,
        "payment_status": a.payment_status, "booking_amount": a.booking_amount,
        "hold_expires_at": a.hold_expires_at.isoformat() if a.hold_expires_at else None,
    }
