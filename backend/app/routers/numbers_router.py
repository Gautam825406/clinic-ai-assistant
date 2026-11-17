"""Numbers router — v1.1+ own-number registration (PRD Sec 6 / CONTRACT).

Every clinic has its OWN phone/WhatsApp number, verified via OTP in its
dashboard — never one shared number across clinics. Delegates to the
numbers module when it exists; until then a deterministic simulated
fallback runs (dev OTP is returned only in DEMO_MODE, never in production).
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..models import ClinicNumber, User
from ..schemas import OTPRequest, OTPVerify
from ..security import get_current_user, hash_password, verify_password

router = APIRouter(tags=["numbers"])

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _number_dict(n: ClinicNumber) -> dict:
    return {"id": n.id, "tenant_id": n.tenant_id, "phone_number": n.phone_number,
            "purpose": n.purpose, "verified": n.verified,
            "created_at": n.created_at.isoformat() if n.created_at else None}


@router.post("/numbers/request-otp")
def request_otp(body: OTPRequest, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    try:
        from ..numbers import request_otp as _request_otp
    except Exception:
        _request_otp = None
    if _request_otp is not None:
        return _request_otp(db, user.tenant_id, body.phone_number, body.purpose)
    import random

    otp = f"{random.randint(0, 999999):06d}"
    now = datetime.now()
    number = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == user.tenant_id,
        ClinicNumber.phone_number == body.phone_number).first()
    if number is None:
        number = ClinicNumber(tenant_id=user.tenant_id, phone_number=body.phone_number,
                              purpose=body.purpose, verified=False)
        db.add(number)
    number.otp_hash = hash_password(otp)
    number.otp_expires_at = now + timedelta(minutes=OTP_TTL_MINUTES)
    number.otp_attempts = 0
    db.commit()
    result: dict = {"status": "otp_sent", "phone_number": body.phone_number,
                    "purpose": body.purpose, "expires_in_minutes": OTP_TTL_MINUTES}
    if settings.demo_mode:
        # Dev/simulated mode only: the dashboard shows the OTP for testing.
        result["dev_otp"] = otp
    return result


@router.post("/numbers/verify-otp")
def verify_otp(body: OTPVerify, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    try:
        from ..numbers import verify_otp as _verify_otp
    except Exception:
        _verify_otp = None
    if _verify_otp is not None:
        verified = _verify_otp(db, user.tenant_id, body.phone_number, body.otp)
        return {"verified": bool(verified), "phone_number": body.phone_number}
    number = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == user.tenant_id,
        ClinicNumber.phone_number == body.phone_number).first()
    if number is None or not number.otp_hash:
        raise HTTPException(status_code=404, detail="Is number ke liye OTP request nahi mila")
    if number.otp_attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Bahut baar galat OTP — naya OTP maangein")
    if number.otp_expires_at and datetime.now() > number.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP expire ho gaya — naya OTP maangein")
    number.otp_attempts = (number.otp_attempts or 0) + 1
    if not verify_password(body.otp, number.otp_hash):
        db.commit()
        return {"verified": False, "phone_number": body.phone_number}
    number.verified = True
    number.otp_hash = None
    db.commit()
    return {"verified": True, "phone_number": body.phone_number}


@router.get("/numbers/me")
def my_numbers(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    numbers = db.query(ClinicNumber).filter(ClinicNumber.tenant_id == user.tenant_id).all()
    return {"count": len(numbers), "numbers": [_number_dict(n) for n in numbers]}
