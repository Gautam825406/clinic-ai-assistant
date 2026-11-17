"""Auth router — signup / login / token rotation.

Signup creates a Tenant + Clinic + owner User in one step and applies the
General template by default (pilot specialty). The api_token is returned
once, at signup; afterwards it is only ever rotated, never re-shown.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Clinic, Subscription, Tenant, User
from ..schemas import LoginRequest, SignupRequest
from ..security import get_current_user, hash_password, new_api_token, verify_password
from ..specialty_templates import TEMPLATES, apply_template_to_tenant

router = APIRouter(tags=["auth"])


@router.post("/auth/signup", status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    specialty = body.specialty if body.specialty in TEMPLATES else "general"
    tenant = Tenant(name=body.clinic_name, type="clinic", city=body.city,
                    plan="pilot", status="trial")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    # FR-23 hook (config-only): har workspace ko ek Subscription row
    # milti hai — plan 'pilot', status 'trial'. Koi payment / autopay /
    # invoice yahan create nahi hota; billing future step hai (PRD Sec 13).
    db.add(Subscription(tenant_id=tenant.id, plan="pilot", status="trial", amount=0))
    db.commit()
    clinic = Clinic(
        tenant_id=tenant.id, display_name=body.clinic_name,
        specialty_template_id=specialty,
        fees_text="Consultation Rs. 500 (range, confirm at reception)",
        greeting_text=f"Namaste, {body.clinic_name} mein aapka swagat hai.",
        booking_amount_enabled=True, booking_amount_default=100,
        timely_cancel_hours=4, late_cancel_policy="forfeit", hold_minutes=15,
    )
    db.add(clinic)
    db.commit()
    db.refresh(clinic)
    token = new_api_token()
    owner = User(
        tenant_id=tenant.id, name=body.owner_name, mobile=body.owner_mobile,
        role="owner", password_hash=hash_password(body.password),
        api_token=token,
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    apply_template_to_tenant(db, tenant.id, specialty)
    return {
        "tenant_id": tenant.id, "clinic_id": clinic.id, "user_id": owner.id,
        "api_token": token, "clinic_name": body.clinic_name,
        "specialty_template_id": specialty,
        "message": "Clinic AI Assistant workspace ban gaya. Ye api_token sirf ek baar dikhega — ise safe jagah save karein.",
    }


@router.post("/auth/login")
def login(body: LoginRequest, db: Session = Depends(get_db)):
    candidates = db.query(User).filter(User.mobile == body.mobile).all()
    for user in candidates:
        if verify_password(body.password, user.password_hash):
            return {
                "api_token": user.api_token, "tenant_id": user.tenant_id,
                "user_id": user.id, "name": user.name,
                "is_platform_admin": user.is_platform_admin,
            }
    raise HTTPException(status_code=401, detail="Mobile ya password galat hai")


@router.post("/auth/rotate-token")
def rotate_token(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = new_api_token()
    user.api_token = token
    db.commit()
    return {"api_token": token,
            "message": "Naya api_token mil gaya. Purana token ab kaam nahi karega."}
