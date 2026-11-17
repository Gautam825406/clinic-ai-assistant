"""Telephony + STT/TTS tests — PRD Sec 8.

Covers the simulated adapter, inbound-webhook tenant resolution
(Clinic.telephony_number, then ClinicNumber), and the simulated
STT/TTS providers. Live vendor adapters must raise until wired up.
"""
import pytest

from app.database import Base, SessionLocal, init_db
from app.models import Call, ClinicNumber
from app.stt_tts import (
    GroqWhisperSTT,
    SimulatedSTT,
    SimulatedTTS,
    get_stt,
    get_tts,
)
from app.telephony import (
    CALL_LOG,
    ExotelAdapter,
    PlivoAdapter,
    SimulatedTelephonyAdapter,
    TwilioAdapter,
    get_telephony_adapter,
    handle_inbound_webhook,
)
from helpers import seed_clinic


@pytest.fixture()
def db():
    init_db()
    session = SessionLocal()
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    yield session
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.commit()
    session.close()


@pytest.fixture(autouse=True)
def _clear_call_log():
    CALL_LOG.clear()
    yield
    CALL_LOG.clear()


# --------------------------------------------------------------------------
# Simulated adapter
# --------------------------------------------------------------------------
def test_simulated_adapter_answer_and_speak():
    adapter = SimulatedTelephonyAdapter(clinic_name="Care Plus Clinic")
    out = adapter.answer("CA123", "+919999999999")
    assert out["status"] == "answered"
    assert out["call_sid"] == "CA123"
    assert "Care Plus Clinic" in out["greeting"]
    assert "digital assistant hoon" in out["greeting"]
    assert adapter.speak("Namaste") == "Namaste"
    assert len(CALL_LOG) >= 2


def test_get_telephony_adapter_defaults_to_simulated():
    assert isinstance(get_telephony_adapter(), SimulatedTelephonyAdapter)


def test_vendor_adapters_raise_not_implemented():
    for cls in (ExotelAdapter, PlivoAdapter, TwilioAdapter):
        adapter = cls(api_key="x", api_secret="y")
        with pytest.raises(NotImplementedError):
            adapter.answer("CA1", "+911111111111")
        with pytest.raises(NotImplementedError):
            adapter.speak("hello")


# --------------------------------------------------------------------------
# Inbound webhook
# --------------------------------------------------------------------------
def test_handle_inbound_webhook_creates_call_row(db):
    tenant, clinic, _ = seed_clinic(
        db, telephony_number="+919876543210",
        greeting_text="Namaste! Main Care Plus Clinic ka digital assistant hoon.")
    out = handle_inbound_webhook(
        {"call_sid": "CA123", "from_number": "+919999999999",
         "to_number": "+919876543210"}, db)
    assert out["status"] == "answered"
    assert out["call_sid"] == "CA123"
    assert out["tenant_id"] == tenant.id
    assert "Care Plus Clinic" in out["greeting"]
    call = db.query(Call).filter(
        Call.tenant_id == tenant.id, Call.call_sid == "CA123").first()
    assert call is not None
    assert call.from_number == "+919999999999"
    assert call.direction == "inbound"


def test_handle_inbound_webhook_unknown_number(db):
    seed_clinic(db, telephony_number="+919876543210")
    out = handle_inbound_webhook(
        {"call_sid": "CA999", "from_number": "+919999999999",
         "to_number": "+910000000000"}, db)
    assert out["status"] == "no_tenant"
    assert out["call_sid"] == "CA999"
    assert db.query(Call).filter(Call.call_sid == "CA999").count() == 0


def test_handle_inbound_webhook_resolves_via_clinic_number(db):
    tenant, clinic, _ = seed_clinic(db, telephony_number="")
    db.add(ClinicNumber(tenant_id=tenant.id, phone_number="+919112223334",
                        purpose="voice", verified=True))
    db.commit()
    out = handle_inbound_webhook(
        {"call_sid": "CA200", "from_number": "+919999999999",
         "to_number": "+919112223334"}, db)
    assert out["status"] == "answered"
    assert out["tenant_id"] == tenant.id


def test_handle_inbound_webhook_same_sid_updates_not_duplicates(db):
    tenant, clinic, _ = seed_clinic(db, telephony_number="+919876543210")
    payload = {"call_sid": "CA300", "from_number": "+919999999999",
               "to_number": "+919876543210"}
    handle_inbound_webhook(payload, db)
    handle_inbound_webhook(payload, db)
    assert db.query(Call).filter(
        Call.tenant_id == tenant.id, Call.call_sid == "CA300").count() == 1


# --------------------------------------------------------------------------
# STT / TTS
# --------------------------------------------------------------------------
def test_simulated_stt_returns_input_unchanged():
    stt = SimulatedSTT()
    assert stt.transcribe("kal subah 10 baje appointment chahiye") == \
        "kal subah 10 baje appointment chahiye"
    assert stt.transcribe(b"kal subah 10 baje") == "kal subah 10 baje"


def test_groq_whisper_requires_api_key():
    with pytest.raises(ValueError):
        GroqWhisperSTT(api_key="")


def test_get_stt_simulated_without_key():
    assert isinstance(get_stt(), SimulatedSTT)


def test_simulated_tts_returns_empty_bytes_with_metadata():
    tts = SimulatedTTS()
    assert tts.synthesize("Namaste, appointment confirm hai.") == b""
    assert tts.last_metadata["text"] == "Namaste, appointment confirm hai."
    assert tts.last_metadata["provider"] == "simulated"


def test_get_tts_returns_simulated():
    assert isinstance(get_tts(), SimulatedTTS)
