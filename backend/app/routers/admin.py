"""Admin router — platform view only. Every route requires
is_platform_admin; other users get 403. Delegates to the notifications
module for the daily digest when it exists.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..date_parser import now_ist
from ..models import (
    Appointment, BookingPayment, Call, CallbackTicket, Clinic, Doctor,
    Patient, Subscription, Tenant, User,
)
from ..plans import PLANS
from ..security import audit, get_current_user

router = APIRouter(tags=["admin"])


def _require_admin(user: User) -> None:
    if not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform admin only")


@router.get("/admin/tenants")
def list_tenants(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    out = []
    for t in tenants:
        clinic = db.query(Clinic).filter(Clinic.tenant_id == t.id).first()
        out.append({
            "id": t.id, "name": t.name, "type": t.type, "city": t.city,
            "plan": t.plan, "status": t.status,
            "clinic_name": clinic.display_name if clinic else None,
            "specialty_template_id": clinic.specialty_template_id if clinic else None,
            "appointments": db.query(Appointment).filter(Appointment.tenant_id == t.id).count(),
            "calls": db.query(Call).filter(Call.tenant_id == t.id).count(),
        })
    return {"count": len(out), "tenants": out}


@router.get("/admin/tenants/{tenant_id}")
def tenant_detail(tenant_id: str, user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Super-admin: one clinic in full — tenant + clinic config + doctors
    + subscription + counts. Used by the Admin tab's manage view."""
    _require_admin(user)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    clinic = db.query(Clinic).filter(Clinic.tenant_id == tenant_id).first()
    sub = db.query(Subscription).filter(Subscription.tenant_id == tenant_id).first()
    doctors = db.query(Doctor).filter(Doctor.tenant_id == tenant_id).all()
    return {
        "id": tenant.id, "name": tenant.name, "type": tenant.type,
        "city": tenant.city, "plan": tenant.plan, "status": tenant.status,
        "clinic": {"display_name": clinic.display_name if clinic else None,
                   "specialty_template_id": clinic.specialty_template_id if clinic else None,
                   "booking_amount_enabled": clinic.booking_amount_enabled if clinic else None,
                   "booking_amount_default": clinic.booking_amount_default if clinic else None},
        "doctors": [{"id": d.id, "name": d.name, "speciality": d.speciality,
                     "slot_minutes": d.slot_minutes, "active": d.active} for d in doctors],
        "subscription": {"plan": sub.plan if sub else tenant.plan,
                         "status": sub.status if sub else tenant.status,
                         "autopay_mandate_id": sub.autopay_mandate_id if sub else None},
        "counts": {
            "appointments": db.query(Appointment).filter(Appointment.tenant_id == tenant_id).count(),
            "calls": db.query(Call).filter(Call.tenant_id == tenant_id).count(),
            "patients": db.query(Patient).filter(Patient.tenant_id == tenant_id).count(),
            "open_callbacks": db.query(CallbackTicket).filter(
                CallbackTicket.tenant_id == tenant_id, CallbackTicket.status == "open").count(),
        },
    }


class TenantUpdate(BaseModel):
    status: str | None = None  # trial | active | past_due | paused | cancelled
    plan: str | None = None    # pilot | starter | growth | clinic_plus


@router.patch("/admin/tenants/{tenant_id}")
def update_tenant(tenant_id: str, body: TenantUpdate,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Super-admin manage (PRD Sec 13.7): activate / pause / resume a
    clinic and change its plan — one click, no deploy. Paused clinics
    keep their data; pausing never deletes anything."""
    _require_admin(user)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    valid_status = ("trial", "active", "past_due", "paused", "cancelled")
    if body.status is not None and body.status not in valid_status:
        raise HTTPException(status_code=400, detail=f"status must be one of {valid_status}")
    if body.plan is not None and body.plan not in PLANS:
        raise HTTPException(status_code=400, detail=f"plan must be one of {sorted(PLANS)}")
    changes: dict = {}
    if body.status is not None and body.status != tenant.status:
        changes["status"] = (tenant.status, body.status)
        tenant.status = body.status
    if body.plan is not None and body.plan != tenant.plan:
        changes["plan"] = (tenant.plan, body.plan)
        tenant.plan = body.plan
        sub = db.query(Subscription).filter(Subscription.tenant_id == tenant_id).first()
        if sub is not None:
            sub.plan = body.plan
    db.commit()
    db.refresh(tenant)
    audit(db, tenant_id, user.mobile or user.id, "tenant_update",
          entity="tenant", entity_id=tenant_id,
          detail={"changes": {k: list(v) for k, v in changes.items()}})
    return {"id": tenant.id, "name": tenant.name, "plan": tenant.plan,
            "status": tenant.status, "changes": {k: v[1] for k, v in changes.items()}}


@router.get("/admin/stats")
def platform_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    paid = db.query(func.coalesce(func.sum(BookingPayment.amount), 0)).filter(
        BookingPayment.status == "paid").scalar()
    return {
        "tenants": db.query(Tenant).count(),
        "appointments": db.query(Appointment).count(),
        "calls": db.query(Call).count(),
        "failed_calls": db.query(Call).filter(Call.failure_reason.isnot(None)).count(),
        "booking_payments_paid_rs": int(paid or 0),
    }


@router.get("/admin/failures")
def platform_failures(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    calls = (
        db.query(Call)
        .filter(Call.failure_reason.isnot(None))
        .order_by(Call.started_at.desc()).limit(100).all()
    )
    return {"count": len(calls), "failures": [
        {"id": c.id, "tenant_id": c.tenant_id, "call_sid": c.call_sid,
         "from_number": c.from_number,
         "started_at": c.started_at.isoformat() if c.started_at else None,
         "outcome": c.outcome, "failure_reason": c.failure_reason}
        for c in calls]}


@router.get("/admin/digest/{tenant_id}")
def tenant_digest(tenant_id: str, day: date | None = None,
                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(user)
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    day = day or now_ist().date()
    try:
        from ..notifications import build_daily_digest
    except Exception:
        build_daily_digest = None
    if build_daily_digest is not None:
        return {"tenant_id": tenant_id, "date": day.isoformat(),
                "digest": build_daily_digest(db, tenant_id, day)}
    appts = (
        db.query(Appointment)
        .filter(Appointment.tenant_id == tenant_id, Appointment.date == day)
        .order_by(Appointment.slot_start).all()
    )
    lines = [f"Aaj ki list — {tenant.name} — {day.isoformat()} ({len(appts)} appointments)"]
    for a in appts:
        lines.append(f"{a.slot_start.strftime('%H:%M')} — {a.reason_line or 'appointment'} — {a.status}")
    return {"tenant_id": tenant_id, "date": day.isoformat(),
            "digest": "\n".join(lines)}
