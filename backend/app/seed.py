"""Demo seed — ONE demo clinic only, clearly flagged DEMO.

The demo clinic is a General / GP clinic (pilot specialty). Its name and
doctor are placeholders, not a real clinic or doctor: "Care Plus Clinic —
DEMO" / "Dr. Sharma (DEMO)". The pilot clinic's real name/doctor is still
an open decision (D1) — nothing here should be mistaken for it.

Idempotent: a second seed_demo() returns the existing demo tenant.
The owner user's demo password is generated randomly, printed once, and
returned — it is never hardcoded and never stored in plain text.
"""
import os
import secrets

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import (
    Appointment, AppointmentType, AuditLog, BookingPayment, Call, CallbackTicket,
    Clinic, ClinicNumber, Doctor, Holiday, Notification, Patient, Schedule,
    SpecialtyTemplate, Subscription, Tenant, User,
)
from .security import hash_password, new_api_token
from .specialty_templates import apply_template_to_tenant

DEMO_CLINIC_NAME = "Care Plus Clinic — DEMO"
DEMO_DOCTOR_NAME = "Dr. Sharma (DEMO)"
DEMO_MOBILE = "+919000000000"  # dummy number, clearly fake
DEMO_WHATSAPP = "+91-90000-00000"  # dummy number, clearly fake


def _find_demo_tenant(db: Session):
    clinic = db.query(Clinic).filter(Clinic.display_name == DEMO_CLINIC_NAME).first()
    if clinic is None:
        return None, None
    tenant = db.query(Tenant).filter(Tenant.id == clinic.tenant_id).first()
    return tenant, clinic


def seed_demo(db: Session) -> dict:
    """Create (or return) the single DEMO clinic.

    Returns {tenant_id, clinic_id, doctor_id, api_token, demo_password}.
    demo_password is set only on first creation; on an idempotent re-run
    it is None (the password was printed once at creation).
    """
    tenant, clinic = _find_demo_tenant(db)
    if tenant is not None and clinic is not None:
        doctor = (
            db.query(Doctor)
            .filter(Doctor.tenant_id == tenant.id)
            .order_by(Doctor.name)
            .first()
        )
        owner = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.role == "owner")
            .first()
        )
        return {
            "tenant_id": tenant.id,
            "clinic_id": clinic.id,
            "doctor_id": doctor.id if doctor else None,
            "api_token": owner.api_token if owner else None,
            "demo_password": None,
        }

    demo_password = secrets.token_urlsafe(12)
    tenant = Tenant(name=DEMO_CLINIC_NAME, type="clinic", city="Bangalore",
                    plan="pilot", status="trial")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    clinic = Clinic(
        tenant_id=tenant.id,
        display_name=DEMO_CLINIC_NAME,
        specialty_template_id="general",
        address="DEMO address — not a real clinic",
        google_maps_text="",
        fees_text="Consultation Rs. 500 (range, confirm at reception)",
        greeting_text="Namaste, Care Plus Clinic mein aapka swagat hai. Main aapki appointment mein madad kar sakta hoon.",
        languages="hinglish",
        whatsapp_doctor=DEMO_WHATSAPP,
        whatsapp_reception=DEMO_WHATSAPP,
        telephony_number=DEMO_WHATSAPP,
        opening_time="09:00",
        closing_time="21:00",
        booking_amount_enabled=True,
        booking_amount_default=100,
        timely_cancel_hours=4,
        late_cancel_policy="forfeit",
        hold_minutes=15,
        reminder_hours_before=2,
        no_show_grace_minutes=15,
    )
    db.add(clinic)
    db.commit()
    db.refresh(clinic)

    owner = User(
        tenant_id=tenant.id,
        name="Demo Owner",
        mobile=DEMO_MOBILE,
        email=None,
        role="owner",
        password_hash=hash_password(demo_password),
        api_token=new_api_token(),
        is_platform_admin=False,
    )
    db.add(owner)

    doctor = Doctor(
        tenant_id=tenant.id, name=DEMO_DOCTOR_NAME,
        speciality="General / GP", slot_minutes=15, active=True,
    )
    db.add(doctor)
    db.commit()
    db.refresh(doctor)

    # Mon–Sat (weekday 0–5), two sessions a day: 09:00–13:00 and 16:00–21:00.
    for weekday in range(0, 6):
        for start, end in (("09:00", "13:00"), ("16:00", "21:00")):
            db.add(Schedule(
                tenant_id=tenant.id, doctor_id=doctor.id,
                weekday=weekday, start_time=start, end_time=end,
            ))
    db.add(ClinicNumber(
        tenant_id=tenant.id, phone_number=DEMO_MOBILE,
        purpose="both", verified=False,
    ))
    db.commit()

    apply_template_to_tenant(db, tenant.id, "general")

    print(f"[Clinic AI Assistant] DEMO clinic seeded: {DEMO_CLINIC_NAME}")
    print(f"[Clinic AI Assistant] DEMO owner mobile: {DEMO_MOBILE}")
    print(f"[Clinic AI Assistant] DEMO one-time password: {demo_password}")
    print("[Clinic AI Assistant] DEMO api_token is returned by seed_demo() — store it, it is shown once.")
    return {
        "tenant_id": tenant.id,
        "clinic_id": clinic.id,
        "doctor_id": doctor.id,
        "api_token": owner.api_token,
        "demo_password": demo_password,
    }


def ensure_platform_admin(db: Session) -> dict | None:
    """Platform super-admin bootstrap (PRD Sec 13.7 / Sec 16.3).

    Before v1.3.1 there was NO way to become a platform admin:
    ``is_platform_admin`` defaulted False for every user and no code
    ever set it True, so /admin/* was unreachable in production.
    This is the one supported path, env-only, never in git/chat:
    set PLATFORM_ADMIN_MOBILE + PLATFORM_ADMIN_PASSWORD in the host
    environment (Render > Environment). On boot the user is created
    (own platform tenant) or upgraded to platform admin, and the
    password from the env is applied. With the env vars unset this is
    a no-op and returns None. Returns {user_id, tenant_id, api_token}
    (api_token only when a new user is created).
    """
    mobile = (os.getenv("PLATFORM_ADMIN_MOBILE") or "").strip()
    password = os.getenv("PLATFORM_ADMIN_PASSWORD") or ""
    if not mobile or not password:
        return None
    user = db.query(User).filter(User.mobile == mobile).first()
    if user is not None:
        user.is_platform_admin = True
        user.password_hash = hash_password(password)
        db.commit()
        return {"user_id": user.id, "tenant_id": user.tenant_id, "api_token": None}
    tenant = Tenant(name="Platform Admin", type="clinic", city="", plan="pilot", status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    db.add(Subscription(tenant_id=tenant.id, plan="pilot", status="active", amount=0))
    db.add(Clinic(tenant_id=tenant.id, display_name="Platform Admin",
                  specialty_template_id="general"))
    user = User(tenant_id=tenant.id, name="Platform Admin", mobile=mobile,
                role="owner", password_hash=hash_password(password),
                api_token=new_api_token(), is_platform_admin=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"user_id": user.id, "tenant_id": tenant.id, "api_token": user.api_token}


def delete_demo(db: Session) -> dict:
    """Remove the demo tenant and ALL of its rows. Returns {deleted: bool}."""
    tenant, clinic = _find_demo_tenant(db)
    if tenant is None:
        return {"deleted": False}
    tid = tenant.id
    for model in (
        AuditLog, Notification, BookingPayment, Appointment, Call,
        CallbackTicket, Patient, ClinicNumber, Holiday, Schedule,
        AppointmentType, SpecialtyTemplate, Doctor, Subscription, User,
    ):
        db.query(model).filter(model.tenant_id == tid).delete(synchronize_session=False)
    db.query(Clinic).filter(Clinic.tenant_id == tid).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tid).delete(synchronize_session=False)
    db.commit()
    return {"deleted": True}


if __name__ == "__main__":
    from .database import init_db

    init_db()
    db = SessionLocal()
    try:
        print(seed_demo(db))
    finally:
        db.close()
