"""Auth: salted password hash (PBKDF2) + per-user API token.
First account on a workspace is the owner. No passwords are ever invented
or stored in plain text; tokens are shown once at signup and then hashed
only by lookup (token itself is a random secret stored for lookup — MVP
trade-off documented in README; rotate via /auth/rotate-token).
"""
import hashlib
import hmac
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def new_api_token() -> str:
    return "cav_" + secrets.token_urlsafe(32)


def get_current_user(x_api_key: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="X-API-Key header required")
    user = db.query(User).filter(User.api_token == x_api_key).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return user


def require_tenant(user: User, tenant_id: str) -> None:
    if user.is_platform_admin:
        return
    if user.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Not found")


def audit(db: Session, tenant_id: str, actor: str, action: str, entity: str = "", entity_id: str = "", detail: dict | None = None):
    from .models import AuditLog
    db.add(AuditLog(tenant_id=tenant_id, actor=actor, action=action, entity=entity, entity_id=entity_id, detail=detail or {}))
    db.commit()
