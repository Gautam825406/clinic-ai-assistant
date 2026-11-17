"""WhatsApp messaging — PRD v1.1 Appendix A (templates 1-7), FR-11/FR-12/FR-26.

Display name everywhere is "Clinic AI Assistant". The gateway/payment
provider name is NEVER shown to patients (FR-26, Section 12.1).

Delivery is via an approved WhatsApp Business BSP only. Personal-number
automation is never used: when no BSP token is configured this module
runs in simulated mode (local dev / tests) and a BSP stub raises a clear
error instead of silently pretending to send.
"""
import logging
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from .config import settings
from .date_parser import format_confirm_date, now_ist
from .models import Appointment, CallbackTicket, Clinic, Doctor, Notification, Patient, Tenant

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Appendix A — template drafts (BSP approval). Placeholders are {{name}}.
# ---------------------------------------------------------------------------
TEMPLATES = {
    "NEW_BOOKING": (
        "New Appointment — {{clinic_name}}\n"
        "Patient: {{patient_name}} ({{mobile_last4}})\n"
        "{{date}}, {{time}} — {{doctor_name}}\n"
        "Reason: {{reason_line}}\n"
        "Token: {{token_no}}\n"
        "Booking amount: {{booking_amount_line}}"
    ),
    "PATIENT_CONFIRMATION": (
        "Namaste {{patient_name}}, aapka appointment confirm hai.\n"
        "{{clinic_name}}, {{doctor_name}}\n"
        "{{date}} — {{time}} | Token: {{token_no}}\n"
        "10 min pehle aayein. Change karna ho to is number par call karein."
    ),
    "DAILY_DIGEST": (
        "Aaj ki list — {{clinic_name}} ({{date}})\n"
        "{{entries_text}}\n"
        "Total: {{total_count}} | Callbacks pending: {{callback_count}}"
    ),
    "CALLBACK_ALERT": (
        "Callback chahiye: {{patient_name}} ({{mobile}})\n"
        "Reason: {{reason}} | Call time: {{call_time}}\n"
        "Dashboard mein Done mark karein."
    ),
    "BOOKING_PAYMENT_LINK": (
        "Slot {{hold_minutes}} min hold hai. Confirm ke liye Rs. {{booking_amount}} pay karein:\n"
        "{{payment_link}} — aayenge to fees mein adjust hoga."
    ),
    "PAYMENT_RECEIVED": (
        "Rs. {{booking_amount}} mil gaya — CONFIRMED. {{date}} {{time}}, Token {{token_no}}. "
        "Baaki fees visit par: Rs. {{balance_fees}}."
    ),
    "REFUND_UPDATE": (
        "Rs. {{booking_amount}} refund: {{refund_status}} ({{reason_line}}). "
        "Paise original UPI par, gateway ke samay se."
    ),
}

TEMPLATE_NAMES = tuple(TEMPLATES.keys())

# In-memory outbox for simulated mode / tests. Never contains secrets.
OUTBOX: list[dict] = []


def _fmt_time(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%I:%M %p").lstrip("0")


def _fmt_date(dt: datetime | None) -> str:
    if not dt:
        return ""
    return format_confirm_date(dt)


def _last4(mobile: str | None) -> str:
    digits = "".join(ch for ch in (mobile or "") if ch.isdigit())
    return digits[-4:] if digits else ""


def render_template(name: str, vars: dict | None = None) -> str:
    """Render one Appendix A template. Raises ValueError for unknown names.

    ``vars`` keys are the {{placeholder}} names. For DAILY_DIGEST callers
    may pass ``entries`` (list of dicts/str) or a pre-built ``entries_text``.
    """
    if name not in TEMPLATES:
        raise ValueError(f"Unknown WhatsApp template: {name!r}")
    data = dict(vars or {})

    if name == "DAILY_DIGEST":
        if "entries_text" not in data:
            entries = data.get("entries") or data.get("appointments") or []
            lines = []
            for entry in entries:
                if isinstance(entry, str):
                    lines.append(entry)
                else:
                    lines.append(
                        f"{entry.get('time', '')} — {entry.get('patient_name', '')}"
                        + (f" — {entry['reason_line']}" if entry.get("reason_line") else "")
                        + (f" (Token {entry['token_no']})" if entry.get("token_no") is not None else "")
                    )
            data["entries_text"] = "\n".join(lines) if lines else "Aaj koi appointment nahi hai."
        data.setdefault("total_count", len(data.get("entries") or []))
        data.setdefault("callback_count", 0)

    text = TEMPLATES[name]
    for key, value in data.items():
        if isinstance(value, (list, dict)):
            continue
        text = text.replace("{{" + key + "}}", str(value))
        text = text.replace("{{ " + key + " }}", str(value))
    # Drop a booking-amount line that has no value rather than sending
    # "Booking amount: " with nothing after it.
    if name == "NEW_BOOKING" and not str(data.get("booking_amount_line") or "").strip():
        text = "\n".join(line for line in text.splitlines() if not line.startswith("Booking amount:"))
    return text.strip()


class WhatsAppProvider:
    """Protocol for WhatsApp delivery. ``send`` returns
    ``dict{status, provider_msg_id}``."""

    def send(self, to: str, template_name: str, body: str) -> dict:
        raise NotImplementedError


class SimulatedWhatsAppProvider(WhatsAppProvider):
    """Simulated mode (PRD Sec 12.1): no BSP call is made. Writes a
    Notification row (status=sent, provider_msg_id="simulated-..."),
    appends to module-level OUTBOX, and logs. Used whenever no
    WHATSAPP_API_TOKEN is configured — always in local dev."""

    def __init__(self, db: Session | None = None, tenant_id: str | None = None):
        self.db = db
        self.tenant_id = tenant_id

    def send(self, to: str, template_name: str, body: str,
             notification_type: str | None = None, tenant_id: str | None = None) -> dict:
        if not to:
            logger.warning("WhatsApp simulated send skipped: no recipient (%s)", template_name)
            return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
        msg_id = f"simulated-{uuid.uuid4().hex[:12]}"
        result = {"status": "sent", "provider_msg_id": msg_id, "to": to,
                  "template_name": template_name}
        OUTBOX.append({"to": to, "template_name": template_name, "body": body, **result})
        logger.info("WhatsApp simulated send to=%s template=%s msg_id=%s", to, template_name, msg_id)
        tid = tenant_id or self.tenant_id
        db = self.db
        if db is not None and tid:
            db.add(Notification(
                tenant_id=tid, type=notification_type or "whatsapp",
                to_number=to, template_name=template_name, status="sent",
                provider_msg_id=msg_id, body=body,
                sent_at=now_ist().replace(tzinfo=None),
            ))
            db.commit()
        elif db is not None:
            logger.warning("WhatsApp simulated send: no tenant_id, Notification row skipped")
        return result


class BSPWhatsAppProvider(WhatsAppProvider):
    """Stub for the real BSP (Business Solution Provider) integration.

    Returned by get_whatsapp() when a token IS configured but no live BSP
    client has been wired up yet. It refuses to send rather than falling
    back to personal-number automation, which the PRD forbids (Sec 12.1).
    """

    def __init__(self, db: Session | None = None, tenant_id: str | None = None):
        self.db = db
        self.tenant_id = tenant_id

    def send(self, to: str, template_name: str, body: str, **kwargs) -> dict:
        raise RuntimeError(
            "WhatsApp BSP not configured: WHATSAPP_API_TOKEN is set but no live "
            "WhatsApp Business BSP client is wired up. Configure the BSP "
            "provider (WHATSAPP_PROVIDER / WHATSAPP_PHONE_NUMBER_ID) before "
            "sending; personal-number automation is never used."
        )


# Backwards-friendly alias.
WhatsAppBSPProvider = BSPWhatsAppProvider


def get_whatsapp(db: Session | None = None, tenant_id: str | None = None) -> WhatsAppProvider:
    """Simulated provider when WHATSAPP_API_TOKEN is empty (always in
    local dev); otherwise the BSP stub, which raises a clear error on send
    until a real BSP client is configured."""
    if not settings.whatsapp_api_token or settings.whatsapp_provider.lower() == "simulated":
        return SimulatedWhatsAppProvider(db, tenant_id)
    return BSPWhatsAppProvider(db, tenant_id)


def send_sms_fallback(to: str, text: str) -> dict:
    """SMS fallback (FR-12: WhatsApp failing that, SMS). Simulated in
    local dev — no SMS provider call is made. Returns {status: "simulated"}."""
    msg_id = f"simulated-sms-{uuid.uuid4().hex[:12]}"
    logger.info("SMS fallback (simulated) to=%s msg_id=%s", to, msg_id)
    return {"status": "simulated", "provider_msg_id": msg_id, "to": to, "text": text}


# ---------------------------------------------------------------------------
# Helpers — resolve tenant-scoped rows, render, send.
# ---------------------------------------------------------------------------

def _tenant_id_of(tenant, appointment: Appointment | None = None) -> str:
    if isinstance(tenant, str):
        return tenant
    if tenant is not None:
        return getattr(tenant, "tenant_id", None) or getattr(tenant, "id", "")
    if appointment is not None:
        return appointment.tenant_id
    return ""


def _resolve(db: Session, tenant, appointment: Appointment):
    """Tenant-scoped lookup of clinic / doctor / patient for an appointment.

    ``tenant`` may be a Tenant, a Clinic, or a tenant-id string.
    """
    tid = _tenant_id_of(tenant, appointment)
    clinic = None
    if isinstance(tenant, Clinic):
        clinic = tenant
    else:
        clinic = db.query(Clinic).filter(Clinic.tenant_id == tid).first()
    doctor = db.query(Doctor).filter(
        Doctor.id == appointment.doctor_id, Doctor.tenant_id == tid).first()
    patient = db.query(Patient).filter(
        Patient.id == appointment.patient_id, Patient.tenant_id == tid).first()
    return tid, clinic, doctor, patient


def _clinic_name(clinic: Clinic | None, tenant) -> str:
    if clinic and clinic.display_name:
        return clinic.display_name
    if isinstance(tenant, Tenant):
        return tenant.name
    return "Clinic"


def _load_appointment(db: Session, appointment) -> Appointment:
    if isinstance(appointment, Appointment):
        return appointment
    row = db.query(Appointment).filter(Appointment.id == appointment).first()
    if row is None:
        raise ValueError(f"Appointment not found: {appointment}")
    return row


def _booking_amount_line(appointment: Appointment) -> str:
    amount = appointment.booking_amount or 0
    if amount <= 0:
        return "Not required"
    if appointment.payment_status == "paid":
        return f"Paid Rs. {amount} (visit par adjust)"
    if appointment.payment_status == "adjusted":
        return f"Rs. {amount} — adjusted"
    if appointment.payment_status == "carry_forward":
        return f"Rs. {amount} — carry-forward"
    if appointment.payment_status in ("refunded", "refund_initiated"):
        return f"Rs. {amount} — refund {appointment.payment_status}"
    if appointment.payment_status == "forfeited":
        return f"Rs. {amount} — forfeited"
    return f"Rs. {amount} pending"


def _base_vars(db: Session, tenant, appointment: Appointment) -> dict:
    tid, clinic, doctor, patient = _resolve(db, tenant, appointment)
    return {
        "tenant_id": tid, "clinic": clinic, "doctor": doctor, "patient": patient,
        "clinic_name": _clinic_name(clinic, tenant),
        "patient_name": patient.name if patient else "",
        "mobile": patient.mobile if patient else "",
        "mobile_last4": _last4(patient.mobile if patient else ""),
        "doctor_name": doctor.name if doctor else "",
        "date": _fmt_date(appointment.slot_start),
        "time": _fmt_time(appointment.slot_start),
        "reason_line": appointment.reason_line or "",
        "token_no": appointment.token_no if appointment.token_no is not None else "",
        "booking_amount": appointment.booking_amount or 0,
        "booking_amount_line": _booking_amount_line(appointment),
    }


def send_booking_summary(db: Session, tenant, appointment) -> dict:
    """FR-11: structured booking summary to doctor/reception WhatsApp
    (Template 1, NEW_BOOKING). Sent only for a real booking — an unpaid
    HELD_PENDING_PAYMENT booking gets no summary (PRD FR-26)."""
    appt = _load_appointment(db, appointment)
    base = _base_vars(db, tenant, appt)
    body = render_template("NEW_BOOKING", base)
    clinic = base["clinic"]
    to = (clinic.whatsapp_doctor or clinic.whatsapp_reception) if clinic else ""
    if not to:
        logger.warning("Booking summary skipped: clinic has no WhatsApp number")
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, base["tenant_id"])
    return provider.send(to, "NEW_BOOKING", body, notification_type="booking_summary")


def send_patient_confirmation(db: Session, tenant, appointment) -> dict:
    """FR-12: patient confirmation WhatsApp (Template 2). Falls back to
    the SMS stub when the patient has no WhatsApp-reachable number recorded
    (here: no mobile at all)."""
    appt = _load_appointment(db, appointment)
    base = _base_vars(db, tenant, appt)
    body = render_template("PATIENT_CONFIRMATION", base)
    to = base["mobile"]
    if not to:
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, base["tenant_id"])
    try:
        return provider.send(to, "PATIENT_CONFIRMATION", body,
                             notification_type="patient_confirmation")
    except RuntimeError:
        return send_sms_fallback(to, body)


def send_payment_link_msg(db: Session, tenant, appointment, link: str, amount=None) -> dict:
    """FR-26 Template 5: UPI payment link to the patient. The gateway name
    is never included in the message."""
    appt = _load_appointment(db, appointment)
    base = _base_vars(db, tenant, appt)
    clinic = base["clinic"]
    body = render_template("BOOKING_PAYMENT_LINK", {
        **base,
        "booking_amount": amount if amount is not None else appt.booking_amount or 0,
        "payment_link": link,
        "hold_minutes": clinic.hold_minutes if clinic else 15,
    })
    to = base["mobile"]
    if not to:
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, base["tenant_id"])
    return provider.send(to, "BOOKING_PAYMENT_LINK", body, notification_type="payment_link")


def send_payment_received_msg(db: Session, tenant, appointment, amount=None, balance_fees=None) -> dict:
    """FR-26 Template 6: payment received — appointment CONFIRMED."""
    appt = _load_appointment(db, appointment)
    base = _base_vars(db, tenant, appt)
    amt = amount if amount is not None else appt.booking_amount or 0
    if balance_fees is None:
        balance_fees = _balance_fees(db, base["tenant_id"], appt, amt)
    body = render_template("PAYMENT_RECEIVED", {
        **base, "booking_amount": amt, "balance_fees": balance_fees,
    })
    to = base["mobile"]
    if not to:
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, base["tenant_id"])
    return provider.send(to, "PAYMENT_RECEIVED", body, notification_type="payment_received")


def _balance_fees(db: Session, tenant_id: str, appointment: Appointment, amount: int):
    """Remaining fees at visit = configured fees minus booking amount.
    Fees text is free text (e.g. "Rs. 500"); if no number can be parsed
    the balance is honestly shown as '—' rather than invented."""
    from .models import AppointmentType
    fees_text = ""
    if appointment.appointment_type_id:
        atype = db.query(AppointmentType).filter(
            AppointmentType.id == appointment.appointment_type_id,
            AppointmentType.tenant_id == tenant_id).first()
        if atype:
            fees_text = atype.fees_text or ""
    import re
    m = re.search(r"(\d+)", fees_text or "")
    if m:
        return max(0, int(m.group(1)) - int(amount or 0))
    return "—"


def send_refund_msg(db: Session, tenant, appointment, amount=None,
                    refund_status: str = "initiated", reason_line: str = "",
                    reason: str | None = None) -> dict:
    """FR-26 Template 7: refund update to the patient. ``refund_status``
    accepts 'initiated'/'refund_initiated' (refund shuru ho gaya) or
    'completed'/'refunded' (refund mil gaya), or a Hinglish string as-is."""
    appt = _load_appointment(db, appointment)
    base = _base_vars(db, tenant, appt)
    status_map = {
        "initiated": "shuru ho gaya — refund initiate ho gaya hai",
        "refund_initiated": "shuru ho gaya — refund initiate ho gaya hai",
        "completed": "mil gaya",
        "refunded": "mil gaya",
        "paid": "mil gaya",
    }
    status_text = status_map.get(str(refund_status).lower(), str(refund_status))
    body = render_template("REFUND_UPDATE", {
        **base,
        "booking_amount": amount if amount is not None else appt.booking_amount or 0,
        "refund_status": status_text,
        "reason_line": reason_line or reason or appt.reason_line or "cancel",
    })
    to = base["mobile"]
    if not to:
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, base["tenant_id"])
    return provider.send(to, "REFUND_UPDATE", body, notification_type="refund_update")


def send_callback_alert(db: Session, tenant, ticket) -> dict:
    """FR-19: callback alert to doctor/reception WhatsApp (Template 4)."""
    if isinstance(ticket, dict):
        name, mobile, reason = ticket.get("name", ""), ticket.get("mobile", ""), ticket.get("reason", "")
        call_time = ticket.get("call_time") or ticket.get("created_at") or ""
        tid = _tenant_id_of(tenant)
        clinic = db.query(Clinic).filter(Clinic.tenant_id == tid).first() if tid else None
        clinic_name = _clinic_name(clinic, tenant)
    else:
        ticket_row: CallbackTicket = ticket
        name, mobile, reason = ticket_row.name, ticket_row.mobile, ticket_row.reason
        call_time = ticket_row.created_at.strftime("%d %b, %I:%M %p") if ticket_row.created_at else ""
        tid = _tenant_id_of(tenant, ticket_row)
        clinic = db.query(Clinic).filter(Clinic.tenant_id == tid).first()
        clinic_name = _clinic_name(clinic, tenant)
    body = render_template("CALLBACK_ALERT", {
        "clinic_name": clinic_name, "patient_name": name, "mobile": mobile,
        "reason": reason, "call_time": call_time,
    })
    to = (clinic.whatsapp_doctor or clinic.whatsapp_reception) if clinic else ""
    if not to:
        return {"status": "failed", "provider_msg_id": None, "error": "no_recipient"}
    provider = get_whatsapp(db, tid)
    return provider.send(to, "CALLBACK_ALERT", body, notification_type="callback_alert")
