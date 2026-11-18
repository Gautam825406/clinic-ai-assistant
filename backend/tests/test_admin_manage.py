"""Super-admin manage tests — PRD Sec 13.7 / 16.3 (v1.3.1 fix: before
this, is_platform_admin could never become True, so /admin/* was
unreachable in production).
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, init_db
from app.main import app
from app.models import Subscription, Tenant, User
from app.security import hash_password, new_api_token
from app.seed import ensure_platform_admin

init_db()
client = TestClient(app)


def _unique_mobile() -> str:
    return "9" + uuid.uuid4().hex[:9].translate(str.maketrans("abcdef", "123456"))[:9]


@pytest.fixture
def admin_client():
    init_db()
    db = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    tenant = Tenant(name="Platform", type="clinic", city="", plan="pilot", status="active")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    admin = User(tenant_id=tenant.id, name="Admin", mobile=_unique_mobile(),
                 role="owner", password_hash=hash_password("admin-pass-123"),
                 api_token=new_api_token(), is_platform_admin=True)
    db.add(admin)
    db.commit()
    token = admin.api_token
    tenant_id = tenant.id
    yield token, tenant_id
    db.close()


def _signup_clinic():
    mobile = _unique_mobile()
    r = client.post("/auth/signup", json={
        "clinic_name": "Managed Clinic", "owner_name": "Owner",
        "owner_mobile": mobile, "password": "test-password-123",
        "city": "Bangalore", "specialty": "general",
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_admin_can_list_detail_pause_resume_and_change_plan(admin_client):
    token, _ = admin_client
    clinic = _signup_clinic()
    tid = clinic["tenant_id"]
    h = {"X-API-Key": token}
    r = client.get("/admin/tenants", headers=h)
    assert r.status_code == 200, r.text
    assert any(t["id"] == tid for t in r.json()["tenants"])
    r = client.get(f"/admin/tenants/{tid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["clinic"]["display_name"] == "Managed Clinic"
    assert r.json()["subscription"]["plan"] == "pilot"
    # Pause, then resume, then change plan — all one-click, no deploy.
    r = client.patch(f"/admin/tenants/{tid}", json={"status": "paused"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "paused"
    r = client.patch(f"/admin/tenants/{tid}", json={"status": "active", "plan": "starter"}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active" and r.json()["plan"] == "starter"
    r = client.get(f"/admin/tenants/{tid}", headers=h)
    assert r.json()["subscription"]["plan"] == "starter"
    # Invalid values rejected.
    assert client.patch(f"/admin/tenants/{tid}", json={"status": "deleted"}, headers=h).status_code == 400
    assert client.patch(f"/admin/tenants/{tid}", json={"plan": "mega"}, headers=h).status_code == 400
    assert client.get("/admin/tenants/nope", headers=h).status_code == 404


def test_non_admin_gets_403_on_admin_manage(admin_client):
    clinic = _signup_clinic()
    h = {"X-API-Key": clinic["api_token"]}
    assert client.get("/admin/tenants", headers=h).status_code == 403
    assert client.patch(f"/admin/tenants/{clinic['tenant_id']}",
                        json={"status": "paused"}, headers=h).status_code == 403


def test_ensure_platform_admin_bootstraps_from_env(monkeypatch):
    init_db()
    db = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    # No env vars -> no-op.
    assert ensure_platform_admin(db) is None
    mobile = _unique_mobile()
    monkeypatch.setenv("PLATFORM_ADMIN_MOBILE", mobile)
    monkeypatch.setenv("PLATFORM_ADMIN_PASSWORD", "super-secret-123")
    result = ensure_platform_admin(db)
    assert result is not None
    user = db.query(User).filter(User.mobile == mobile).first()
    assert user is not None and user.is_platform_admin is True
    # Login works with that password.
    r = client.post("/auth/login", json={"mobile": mobile, "password": "super-secret-123"})
    assert r.status_code == 200, r.text
    assert r.json()["is_platform_admin"] is True
    # Second boot upgrades the same user, no duplicate.
    assert ensure_platform_admin(db)["user_id"] == user.id
    assert db.query(User).filter(User.mobile == mobile).count() == 1
    db.close()
