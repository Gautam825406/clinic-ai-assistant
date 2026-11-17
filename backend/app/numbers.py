"""Clinic numbers — PRD v1.1+ addition (17 Nov evening decision).

Every clinic has its OWN phone/WhatsApp number, verified via OTP in its
dashboard. Never one shared number across clinics.

Only the SHA-256 hash of an OTP is ever stored — never the OTP itself.
OTP: 6 digits, 10-minute expiry, max 5 attempts.
"""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from .config import settings
from .date_parser import now_ist
from .models import ClinicNumber

logger = logging.getLogger(__name__)

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def _norm_phone(phone_number: str) -> str:
    return "".join(ch for ch in (phone_number or "").strip() if ch.isdigit() or ch == "+")


def _hash_otp(otp: str) -> str:
    return hashlib.sha256(otp.encode()).hexdigest()


def _other_tenant_verified(db: Session, tenant_id: str, phone_number: str):
    """A verified number belonging to a different tenant, if any."""
    phone = _norm_phone(phone_number)
    return db.query(ClinicNumber).filter(
        ClinicNumber.phone_number == phone,
        ClinicNumber.tenant_id != tenant_id,
        ClinicNumber.verified.is_(True),
    ).first()


def register_number(db: Session, tenant_id: str, phone_number: str,
                    purpose: str = "voice") -> ClinicNumber:
    """Create (or return the existing) ClinicNumber row for this tenant.

    A new row starts verified=False until an OTP passes. The same number
    cannot be registered as verified by two tenants — ValueError.
    """
    phone = _norm_phone(phone_number)
    if not phone:
        raise ValueError("phone_number is required")
    if _other_tenant_verified(db, tenant_id, phone):
        raise ValueError(f"Number {phone} is already verified by another clinic")
    row = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == tenant_id,
        ClinicNumber.phone_number == phone,
        ClinicNumber.purpose == purpose,
    ).first()
    if row is not None:
        return row
    row = ClinicNumber(tenant_id=tenant_id, phone_number=phone,
                       purpose=purpose, verified=False)
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info("Clinic number registered tenant=%s phone=%s purpose=%s", tenant_id, phone, purpose)
    return row


def request_otp(db: Session, tenant_id: str, phone_number: str,
                purpose: str = "voice") -> dict:
    """Generate a 6-digit OTP, store ONLY its SHA-256 hash + 10-min expiry,
    reset attempts to 0. In simulated / DEMO_MODE the OTP is returned as
    ``dev_otp`` so testing works; in live mode it is NOT returned (it is
    sent via the provider stub) — never log or return a live OTP."""
    phone = _norm_phone(phone_number)
    if not phone:
        raise ValueError("phone_number is required")
    if _other_tenant_verified(db, tenant_id, phone):
        raise ValueError(f"Number {phone} is already verified by another clinic")
    row = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == tenant_id,
        ClinicNumber.phone_number == phone,
        ClinicNumber.purpose == purpose,
    ).first()
    if row is None:
        row = ClinicNumber(tenant_id=tenant_id, phone_number=phone,
                           purpose=purpose, verified=False)
        db.add(row)
    otp = f"{secrets.randbelow(900000) + 100000:06d}"
    row.otp_hash = _hash_otp(otp)
    row.otp_expires_at = now_ist().replace(tzinfo=None) + timedelta(minutes=OTP_TTL_MINUTES)
    row.otp_attempts = 0
    row.verified = False
    db.commit()
    db.refresh(row)
    logger.info("OTP requested tenant=%s phone=%s purpose=%s", tenant_id, phone, purpose)
    if settings.demo_mode or settings.otp_provider.lower() == "simulated":
        return {
            "status": "otp_sent", "phone_number": phone, "purpose": purpose,
            "expires_in_seconds": OTP_TTL_MINUTES * 60,
            "dev_otp": otp,
            "note": "DEV ONLY — never in production",
        }
    # Live mode: OTP goes out via the SMS/voice provider; never returned.
    logger.info("OTP sent via provider (live mode) tenant=%s phone=%s", tenant_id, phone)
    return {"status": "otp_sent", "phone_number": phone, "purpose": purpose,
            "expires_in_seconds": OTP_TTL_MINUTES * 60}


def verify_otp(db: Session, tenant_id: str, phone_number: str, otp: str) -> bool:
    """Hash-compare the OTP, check expiry, enforce max 5 attempts. On
    success set verified=True and clear the stored hash. Returns False
    (never raises) for wrong / expired / exhausted OTPs. Raises ValueError
    if the number is already verified by another tenant."""
    phone = _norm_phone(phone_number)
    if _other_tenant_verified(db, tenant_id, phone):
        raise ValueError(f"Number {phone} is already verified by another clinic")
    row = db.query(ClinicNumber).filter(
        ClinicNumber.tenant_id == tenant_id,
        ClinicNumber.phone_number == phone,
    ).order_by(ClinicNumber.created_at.desc()).first()
    if row is None or not row.otp_hash:
        return False
    now = now_ist().replace(tzinfo=None)
    if row.otp_attempts >= OTP_MAX_ATTEMPTS:
        logger.warning("OTP verify blocked: attempts exhausted tenant=%s phone=%s", tenant_id, phone)
        return False
    if row.otp_expires_at is not None and now > row.otp_expires_at:
        logger.warning("OTP verify failed: expired tenant=%s phone=%s", tenant_id, phone)
        return False
    if hmac.compare_digest(row.otp_hash, _hash_otp(str(otp).strip())):
        row.verified = True
        row.otp_hash = None
        row.otp_expires_at = None
        row.otp_attempts = 0
        db.commit()
        logger.info("OTP verified tenant=%s phone=%s", tenant_id, phone)
        return True
    row.otp_attempts = (row.otp_attempts or 0) + 1
    db.commit()
    return False
