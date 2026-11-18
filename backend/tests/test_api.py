"""API tests — signup -> auth -> config -> slots -> booking -> isolation.

Runs against a fresh temp-file SQLite DB (see conftest.py).
"""
import uuid
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.database import SessionLocal, init_db
from app.main import app

init_db()
client = TestClient(app)


def _unique_mobile() -> str:
    return "9" + uuid.uuid4().hex[:9].translate(str.maketrans("abcdef", "123456"))[:9]


def _signup(clinic_name="Test Clinic"):
    mobile = _unique_mobile()
    r = client.post("/auth/signup", json={
        "clinic_name": clinic_name, "owner_name": "Test Owner",
        "owner_mobile": mobile, "password": "test-password-123",
        "city": "Bangalore", "specialty": "general",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["api_token"].startswith("cav_")
    assert body["specialty_template_id"] == "general"
    return body


def _headers(token: str) -> dict:
    return {"X-API-Key": token}


def _tomorrow() -> date:
    return date.today() + timedelta(days=1)


def _setup_doctor(token: str, day: date):
    r = client.post("/clinics/me/doctors",
                    json={"name": "Dr. Sharma (TEST)", "speciality": "General / GP",
                          "slot_minutes": 15}, headers=_headers(token))
    assert r.status_code == 201, r.text
    doctor_id = r.json()["id"]
    r = client.post("/clinics/me/schedules",
                    json={"doctor_id": doctor_id, "weekday": day.weekday(),
                          "start_time": "09:00", "end_time": "13:00"},
                    headers=_headers(token))
    assert r.status_code == 201, r.text
    return doctor_id


def test_health_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["app"] == "Clinic AI Assistant"
    assert r.json()["version"] == "1.1.0"


def test_open_decisions_lists_d1_open():
    r = client.get("/api/open-decisions")
    assert r.status_code == 200
    decisions = {d["id"]: d for d in r.json()["decisions"]}
    assert decisions["D1"]["status"] == "OPEN"
    assert "D9" in decisions
    assert decisions["D9"]["default"].startswith("ON, Rs. 100")


def test_401_without_api_key():
    r = client.get("/clinics/me")
    assert r.status_code == 401


def test_signup_config_slots_booking_flow():
    signup = _signup("Flow Clinic")
    token = signup["api_token"]
    day = _tomorrow()

    # Config patch (FR-17)
    r = client.patch("/clinics/me", json={"fees_text": "Consultation Rs. 400 (range, confirm at reception)"},
                     headers=_headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["fees_text"].startswith("Consultation Rs. 400")

    doctor_id = _setup_doctor(token, day)

    # Slots generation — v1.3.2: the doctor already has the auto-created
    # default hours (Mon–Sat 09:00–13:00 + 16:00–21:00 = 36 slots at 15
    # min); on a Sunday only the manual 09:00–13:00 row exists (16 slots).
    # The manual schedule POST above is now idempotent, so it must not
    # duplicate any slot.
    r = client.get(f"/slots?doctor_id={doctor_id}&date={day.isoformat()}", headers=_headers(token))
    assert r.status_code == 200, r.text
    slots = r.json()["slots"]
    expected = 36 if day.weekday() < 6 else 16
    assert len(slots) == expected, slots
    assert slots[0].endswith("T09:00:00")
    assert len(set(slots)) == len(slots)  # no duplicate slots

    # Manual booking
    r = client.post("/appointments", json={
        "doctor_id": doctor_id, "patient_name": "Rahul Verma",
        "patient_mobile": "9000000001", "date": day.isoformat(),
        "slot_start": slots[0], "reason_line": "bukhaar, 2 din se",
        "source": "manual"}, headers=_headers(token))
    assert r.status_code == 201, r.text
    appt = r.json()
    assert appt["status"] == "CONFIRMED"
    assert appt["patient_name"] == "Rahul Verma"

    # Double-booking the same slot is rejected
    r = client.post("/appointments", json={
        "doctor_id": doctor_id, "patient_name": "Doosra Patient",
        "patient_mobile": "9000000002", "date": day.isoformat(),
        "slot_start": slots[0], "reason_line": "sardi",
        "source": "manual"}, headers=_headers(token))
    assert r.status_code in (400, 409), r.text

    # The booked slot is gone from the slot list
    r = client.get(f"/slots?doctor_id={doctor_id}&date={day.isoformat()}", headers=_headers(token))
    assert slots[0] not in r.json()["slots"]


def test_fresh_doctor_gets_default_schedule_and_books():
    """v1.3.2 regression test for the live-test failure (18 Nov 2026):
    a brand-new clinic adds a doctor and books, WITHOUT anyone manually
    creating a schedule. Before the fix this gave 0 slots for 19/20 Nov
    and booking failed with an opaque "[object Object]" error."""
    signup = _signup("Fresh Clinic")
    token = signup["api_token"]
    tomorrow = _tomorrow()
    day_after = _tomorrow() + timedelta(days=1)

    r = client.post("/clinics/me/doctors",
                    json={"name": "Dr. Fresh", "speciality": "General / GP",
                          "slot_minutes": 15}, headers=_headers(token))
    assert r.status_code == 201, r.text
    assert r.json()["schedules_created"] == 12  # Mon–Sat x 2 sessions
    doctor_id = r.json()["id"]

    slots_tomorrow = client.get(
        f"/slots?doctor_id={doctor_id}&date={tomorrow.isoformat()}",
        headers=_headers(token)).json()["slots"]
    slots_day_after = client.get(
        f"/slots?doctor_id={doctor_id}&date={day_after.isoformat()}",
        headers=_headers(token)).json()["slots"]
    # Both days are weekdays in the live-test window (Thu 19 / Fri 20 Nov);
    # on a Sunday the clinic is closed, so only assert >0 for weekdays.
    if tomorrow.weekday() < 6:
        assert len(slots_tomorrow) > 0, slots_tomorrow
    if day_after.weekday() < 6:
        assert len(slots_day_after) > 0, slots_day_after
    day, slots = (tomorrow, slots_tomorrow) if tomorrow.weekday() < 6 else (day_after, slots_day_after)
    assert len(slots) > 0

    r = client.post("/appointments", json={
        "doctor_id": doctor_id, "patient_name": "Rahul Verma",
        "patient_mobile": "9000000001", "date": day.isoformat(),
        "slot_start": slots[0], "reason_line": "bukhaar, 2 din se",
        "source": "manual"}, headers=_headers(token))
    assert r.status_code == 201, r.text
    appt = r.json()
    assert appt["status"] == "CONFIRMED"
    assert appt["token_no"] == 1

    # Booking a slot that is not offered must fail with a readable
    # STRING message — never a FastAPI validation list ("[object Object]").
    r = client.post("/appointments", json={
        "doctor_id": doctor_id, "patient_name": "Rahul Verma",
        "patient_mobile": "9000000001", "date": day.isoformat(),
        "slot_start": slots[0].replace("T09:00:00", "T03:00:00"),
        "reason_line": "bukhaar", "source": "manual"}, headers=_headers(token))
    assert r.status_code == 400, r.text
    assert isinstance(r.json()["detail"], str)
    assert "khaali" in r.json()["detail"]


def test_cross_tenant_404():
    first = _signup("Clinic A")
    token_a = first["api_token"]
    day = _tomorrow()
    doctor_id = _setup_doctor(token_a, day)
    r = client.get(f"/slots?doctor_id={doctor_id}&date={day.isoformat()}", headers=_headers(token_a))
    slot = r.json()["slots"][0]
    r = client.post("/appointments", json={
        "doctor_id": doctor_id, "patient_name": "Patient A",
        "patient_mobile": "9000000011", "date": day.isoformat(),
        "slot_start": slot, "reason_line": "bukhaar", "source": "manual"},
        headers=_headers(token_a))
    assert r.status_code == 201, r.text
    appt_id = r.json()["id"]

    second = _signup("Clinic B")
    token_b = second["api_token"]
    # Tenant B cannot read tenant A's appointment — 404, not 403 (no leak).
    r = client.get(f"/appointments/{appt_id}", headers=_headers(token_b))
    assert r.status_code == 404
    # ...nor book on tenant A's doctor, nor see A's today list entry.
    r = client.get(f"/slots?doctor_id={doctor_id}&date={day.isoformat()}", headers=_headers(token_b))
    assert r.status_code == 404
    r = client.get("/clinics/me/today", headers=_headers(token_b))
    assert all(a["id"] != appt_id for a in r.json()["appointments"])


def test_login_and_rotate_token():
    mobile = _unique_mobile()
    r = client.post("/auth/signup", json={
        "clinic_name": "Login Clinic", "owner_name": "Owner",
        "owner_mobile": mobile, "password": "login-pass-123",
        "specialty": "general"})
    assert r.status_code == 201
    token = r.json()["api_token"]
    r = client.post("/auth/login", json={"mobile": mobile, "password": "login-pass-123"})
    assert r.status_code == 200
    assert r.json()["api_token"] == token
    r = client.post("/auth/login", json={"mobile": mobile, "password": "galat-password"})
    assert r.status_code == 401
    r = client.post("/auth/rotate-token", headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["api_token"] != token
    r = client.get("/clinics/me", headers=_headers(token))
    assert r.status_code == 401  # old token dead


def test_seed_demo_idempotent():
    from app.models import Schedule
    from app.seed import delete_demo, seed_demo

    db = SessionLocal()
    try:
        delete_demo(db)
        first = seed_demo(db)
        second = seed_demo(db)
        assert first["tenant_id"] == second["tenant_id"]
        assert first["clinic_id"] == second["clinic_id"]
        assert first["doctor_id"] == second["doctor_id"]
        assert first["api_token"] == second["api_token"]
        assert first["demo_password"]  # generated once, never hardcoded
        assert second["demo_password"] is None  # not re-issued
        schedules = db.query(Schedule).filter(
            Schedule.tenant_id == first["tenant_id"]).all()
        assert len(schedules) == 12  # Mon-Sat x 2 sessions
        assert {s.weekday for s in schedules} == {0, 1, 2, 3, 4, 5}
        delete_demo(db)
        third = seed_demo(db)
        assert third["tenant_id"] != first["tenant_id"]  # fresh tenant after delete
        delete_demo(db)
    finally:
        db.close()


def test_voice_simulate_runs_dialogue_turns():
    signup = _signup("Voice Clinic")
    r = client.post("/voice/simulate",
                    json={"from_number": "+919000000001",
                          "turns": ["Namaste", "Haan",
                                    "Mujhe kal shaam dikhana hai, bukhaar hai"]},
                    headers=_headers(signup["api_token"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["transcript"]) == 6  # 3 turns x (patient + assistant)
    assert body["transcript"][0]["role"] == "patient"
    assert body["state"] == "COLLECT"  # engine is now asking for the name


def test_numbers_otp_flow_simulated():
    signup = _signup("Numbers Clinic")
    token = signup["api_token"]
    r = client.post("/numbers/request-otp",
                    json={"phone_number": "+919000000099", "purpose": "both"},
                    headers=_headers(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "otp_sent"
    dev_otp = r.json().get("dev_otp")
    assert dev_otp  # DEMO_MODE: dev OTP returned for testing
    r = client.post("/numbers/verify-otp",
                    json={"phone_number": "+919000000099", "otp": dev_otp},
                    headers=_headers(token))
    assert r.status_code == 200
    assert r.json()["verified"] is True
    r = client.get("/numbers/me", headers=_headers(token))
    assert any(n["verified"] for n in r.json()["numbers"])
