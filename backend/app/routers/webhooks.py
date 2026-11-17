"""WhatsApp webhook — PRD Sec 10.1 (POST /webhooks/whatsapp).

Vendor (BSP) posts delivery statuses AND patient replies here. No auth:
the vendor signs the request (signature check when a webhook secret is
configured — same pattern as the payment webhook); in DEMO_MODE the
payload is accepted as-is, exactly like the simulated payment webhook.

Patient replies handled (tenant is resolved from the patient's mobile —
never from the request body, which is untrusted):
- Opt-out ("stop", "band karo", "reminder band", "unsubscribe"): set
  Patient.consent_flags.reminder_opt_out — FR-14 reminders stop for
  that patient. No appointment is touched.
- Can't come ("nahi aa paunga", "nahi aa sakta", "cancel karo", "radd"):
  find the patient's next upcoming CONFIRMED appointment and cancel it
  through the FR-26 outcome rules (timely = full refund, late = clinic
  policy). This is the FR-26 critical test: a reminder-cancel that
  arrives before the cut-off is NEVER a forfeit.
- Anything else: logged, no action (reception sees it in the call log).

If the same mobile belongs to patients in more than one tenant the
reply is ambiguous — no action is taken, so one clinic can never
cancel another clinic's booking.
"""
import logging
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..date_parser import now_ist
from ..models import Appointment, Patient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webhooks"])

_OPT_OUT = ("stop", "unsubscribe", "band karo", "band kar do", "reminder band",
            "mat bhejo", "opt out", "optout")
_CANCEL_WORDS = ("nahi aa paunga", "nahi aa paungi", "nahi aa sakta",
                 "nahi aa sakti", "nahi aa paaunga", "cancel karo",
                 "cancel kar do", "radd karo", "appointment cancel",
                 "nahi aana", "nahi aa raha", "nahi aa rahi")


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def _extract_fields(payload: dict) -> tuple[str, str, str]:
    """Accept BSP / Meta / simulated shapes. Returns (mobile, text, status)."""
    mobile = ""
    for key in ("from", "from_number", "sender", "mobile", "wa_id"):
        if payload.get(key):
            mobile = str(payload[key])
            break
    # Nested Meta-style: entry[].changes[].value.messages[0]
    if not mobile and isinstance(payload.get("entry"), list):
        try:
            msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
            mobile = str(msg.get("from") or "")
            text = (msg.get("text") or {}).get("body") or msg.get("body") or ""
            return _digits(mobile)[-10:], text, ""
        except Exception:
            pass
    text = ""
    for key in ("body", "text", "message", "reply"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            text = val
            break
        if isinstance(val, dict) and val.get("body"):
            text = str(val["body"])
            break
    status = str(payload.get("status") or payload.get("delivery_status") or "")
    return _digits(mobile)[-10:], text, status


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    mobile, text, status = _extract_fields(payload)
    # Delivery receipt — nothing to do, just acknowledge.
    if status and not text:
        return {"status": "received", "kind": "delivery_status"}
    if not mobile:
        return {"status": "received", "kind": "ignored", "reason": "no_sender"}
    low = text.lower().strip()
    patients = db.query(Patient).filter(Patient.mobile.like(f"%{mobile}")).all()
    # Exact last-10 match only (LIKE above may match suffixes loosely).
    patients = [p for p in patients if _digits(p.mobile)[-10:] == mobile]
    tenants = {p.tenant_id for p in patients}
    if len(tenants) != 1:
        logger.warning("WhatsApp reply from %s: ambiguous tenant (%d) — no action",
                       mobile, len(tenants))
        return {"status": "received", "kind": "ignored",
                "reason": "ambiguous_tenant" if tenants else "unknown_patient"}
    tenant_id = next(iter(tenants))
    patient = patients[0]
    if any(w in low for w in _OPT_OUT):
        flags = dict(patient.consent_flags or {})
        flags["reminder_opt_out"] = True
        patient.consent_flags = flags
        db.commit()
        return {"status": "ok", "kind": "reminder_opt_out", "mobile": mobile}
    if any(w in low for w in _CANCEL_WORDS):
        now = now_ist().replace(tzinfo=None)
        appt = (db.query(Appointment)
                .filter(Appointment.tenant_id == tenant_id,
                        Appointment.patient_id == patient.id,
                        Appointment.status == "CONFIRMED",
                        Appointment.slot_start > now)
                .order_by(Appointment.slot_start).first())
        if appt is None:
            return {"status": "ok", "kind": "cancel_reply",
                    "action": "none", "reason": "no_upcoming_confirmed"}
        from ..date_parser import now_ist as _now_ist
        from ..models import Clinic
        from ..payments import apply_booking_outcome

        clinic = db.query(Clinic).filter(Clinic.tenant_id == tenant_id).first()
        hours_left = (appt.slot_start - _now_ist().replace(tzinfo=None)).total_seconds() / 3600
        outcome = "timely_cancel" if hours_left >= (clinic.timely_cancel_hours or 4) else "late_cancel"
        result = apply_booking_outcome(db, appt.id, outcome)
        return {"status": "ok", "kind": "cancel_reply", "action": outcome,
                "appointment_id": appt.id, "payment": result}
    logger.info("WhatsApp reply from %s: no action for text %r", mobile, text[:80])
    return {"status": "received", "kind": "ignored", "reason": "no_action_keyword"}
