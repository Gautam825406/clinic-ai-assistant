"""Admin router — platform view only. Every route requires
is_platform_admin; other users get 403. Delegates to the notifications
module for the daily digest when it exists.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..date_parser import now_ist
from ..models import Appointment, BookingPayment, Call, Clinic, Tenant, User
from ..security import get_current_user

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
