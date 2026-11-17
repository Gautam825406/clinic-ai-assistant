"""Telephony adapters — PRD Sec 8.

One interface for every vendor (Exotel / Plivo / Twilio): the dialogue
engine only ever talks to a TelephonyAdapter. Simulated mode logs calls
and returns a canned greeting so the whole flow runs with zero credentials;
a live vendor plugs in as a subclass without any rewrite of the dialogue.
"""
from typing import Protocol

from sqlalchemy.orm import Session

from .config import settings
from .models import Call, Clinic, ClinicNumber

# In-memory log of simulated calls (tests / local dev only).
CALL_LOG: list[dict] = []


def _default_greeting(clinic_name: str = "") -> str:
    name = clinic_name or "Clinic AI Assistant"
    return (
        f"Namaste! Main {name} ka digital assistant hoon. "
        "Ye call record ho rahi hai — kya aap iski consent dete hain?"
    )


class TelephonyAdapter(Protocol):
    """Vendor-neutral telephony interface (PRD Sec 8)."""

    def answer(self, call_sid: str, from_number: str) -> dict:
        """Answer an inbound call. Returns call info incl. greeting."""
        ...

    def speak(self, text: str) -> str:
        """Speak ``text`` to the caller. Returns what was spoken."""
        ...


class SimulatedTelephonyAdapter:
    """Simulated adapter: logs calls, returns a canned greeting.

    No network, no credentials — the default in dev / DEMO_MODE.
    """

    def __init__(self, clinic_name: str = ""):
        self.clinic_name = clinic_name

    def answer(self, call_sid: str, from_number: str) -> dict:
        entry = {"call_sid": call_sid, "from_number": from_number,
                 "status": "answered", "provider": "simulated"}
        CALL_LOG.append(entry)
        return {**entry, "greeting": _default_greeting(self.clinic_name)}

    def speak(self, text: str) -> str:
        CALL_LOG.append({"action": "speak", "text": text, "provider": "simulated"})
        return text


class ExotelAdapter:
    """Exotel live adapter (PRD Sec 8).

    Plug-in point: implement ``answer``/``speak`` with the Exotel
    Voice API (Exotel SID / token from env) and return the same dicts
    as SimulatedTelephonyAdapter. Raises until wired up, so a half-
    configured vendor can never silently pretend a call was answered.
    """

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def answer(self, call_sid: str, from_number: str) -> dict:
        raise NotImplementedError(
            "Exotel live adapter is not wired up yet — set TELEPHONY_API_KEY / "
            "TELEPHONY_API_SECRET and implement the Exotel Voice API call here.")

    def speak(self, text: str) -> str:
        raise NotImplementedError(
            "Exotel live adapter is not wired up yet — implement Exotel TTS here.")


class PlivoAdapter:
    """Plivo live adapter (PRD Sec 8). Same contract as ExotelAdapter."""

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def answer(self, call_sid: str, from_number: str) -> dict:
        raise NotImplementedError(
            "Plivo live adapter is not wired up yet — set TELEPHONY_API_KEY / "
            "TELEPHONY_API_SECRET and implement the Plivo Voice API call here.")

    def speak(self, text: str) -> str:
        raise NotImplementedError(
            "Plivo live adapter is not wired up yet — implement Plivo TTS here.")


class TwilioAdapter:
    """Twilio live adapter (PRD Sec 8). Same contract as ExotelAdapter."""

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def answer(self, call_sid: str, from_number: str) -> dict:
        raise NotImplementedError(
            "Twilio live adapter is not wired up yet — set TELEPHONY_API_KEY / "
            "TELEPHONY_API_SECRET and implement the Twilio Voice API call here.")

    def speak(self, text: str) -> str:
        raise NotImplementedError(
            "Twilio live adapter is not wired up yet — implement Twilio TTS here.")


def get_telephony_adapter() -> TelephonyAdapter:
    """Adapter for the configured provider; simulated when unconfigured."""
    provider = (settings.telephony_provider or "simulated").lower()
    if provider == "exotel":
        return ExotelAdapter(settings.telephony_api_key, settings.telephony_api_secret)
    if provider == "plivo":
        return PlivoAdapter(settings.telephony_api_key, settings.telephony_api_secret)
    if provider == "twilio":
        return TwilioAdapter(settings.telephony_api_key, settings.telephony_api_secret)
    return SimulatedTelephonyAdapter()


def _resolve_tenant_by_number(db: Session, to_number: str):
    """Tenant for an inbound call: the clinic's own number (PRD v1.1).

    Matches Clinic.telephony_number first, then a verified ClinicNumber —
    every clinic has its OWN number, never one shared number.
    """
    if not to_number:
        return None, None
    clinic = db.query(Clinic).filter(Clinic.telephony_number == to_number).first()
    if clinic:
        return clinic.tenant_id, clinic
    number = db.query(ClinicNumber).filter(
        ClinicNumber.phone_number == to_number).first()
    if number:
        clinic = db.query(Clinic).filter(Clinic.tenant_id == number.tenant_id).first()
        return number.tenant_id, clinic
    return None, None


def handle_inbound_webhook(payload: dict, db: Session) -> dict:
    """Handle an inbound-call webhook (PRD Sec 8).

    Resolves the tenant from the called (to) number, creates or updates
    the Call row, and returns the greeting + call_sid for the answer.
    """
    payload = payload or {}
    call_sid = payload.get("call_sid") or payload.get("CallSid") or ""
    from_number = payload.get("from_number") or payload.get("from") \
        or payload.get("From") or payload.get("caller") or ""
    to_number = payload.get("to_number") or payload.get("to") \
        or payload.get("To") or payload.get("called") or ""

    tenant_id, clinic = _resolve_tenant_by_number(db, to_number)
    if not tenant_id:
        return {"status": "no_tenant", "call_sid": call_sid,
                "greeting": _default_greeting(),
                "error": "no clinic found for the called number"}

    call = db.query(Call).filter(
        Call.tenant_id == tenant_id, Call.call_sid == call_sid).first() if call_sid else None
    if call is None:
        call = Call(tenant_id=tenant_id, call_sid=call_sid or "simulated",
                    direction="inbound", from_number=from_number,
                    outcome="in_progress", language_detected="hinglish")
        db.add(call)
    else:
        call.from_number = from_number or call.from_number
        call.outcome = "in_progress"
    db.commit()

    greeting = (clinic.greeting_text if clinic and clinic.greeting_text
                else _default_greeting(clinic.display_name if clinic else ""))
    adapter = get_telephony_adapter()
    if isinstance(adapter, SimulatedTelephonyAdapter):
        adapter.clinic_name = clinic.display_name if clinic else ""
        answered = adapter.answer(call.call_sid, from_number)
        greeting = answered.get("greeting") or greeting
    return {"status": "answered", "call_sid": call.call_sid,
            "tenant_id": tenant_id, "greeting": greeting}
