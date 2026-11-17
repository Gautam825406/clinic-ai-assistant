"""Payments — PRD v1.1 FR-26 (refundable booking amount).

Hard rule (FR-26, Sec 6): payment hua ya nahi, refund ya forfeit — sirf
gateway webhook aur deterministic code decide karega. Patient ke
"maine pay kar diya" bolne se booking confirm nahi hogi. No function in
this module takes patient speech as payment proof: confirmation happens
only in handle_payment_webhook() after a verified gateway payload, or in
manual_confirm() — an explicit reception action for the no-UPI exception,
never a patient claim.

The gateway name (cashfree) is an internal constant only and is NEVER
shown to patients: patient WhatsApp templates contain no gateway name.
UPI / card details are never stored — only gateway id + status.
"""
import hashlib
import hmac
import json
import logging
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from .config import settings
from .date_parser import now_ist
from .models import (
    Appointment, AppointmentType, BookingPayment, Clinic, Notification, Patient,
)

logger = logging.getLogger(__name__)

PAYMENT_GATEWAY_NAME = "cashfree"  # internal only — never shown to patients

# PRD advice: booking amount Rs. 200 se zyada nahi — ye filter hai, entry
# fee nahi. Advice hai, hard cap nahi: clinic ka set amount hi laagu hota
# hai (Open Decision D9 — aakhri shabd clinic ka).
BOOKING_AMOUNT_ADVICE_MAX = 200

_SUCCESS_STATUSES = {"SUCCESS", "PAID", "COMPLETED", "PAYMENT_SUCCESS"}
_FAILURE_STATUSES = {"FAILED", "FAILURE", "USER_DROPPED", "CANCELLED", "DROPPED"}
_REFUND_STATUSES = {"REFUND_SUCCESS", "REFUNDED", "REFUND_COMPLETED"}


# ---------------------------------------------------------------------------
# Amount config
# ---------------------------------------------------------------------------

def booking_amount_for(clinic: Clinic | None, appointment_type: AppointmentType | None = None) -> int:
    """FR-26 amount: disabled -> 0; appointment-type override (not None)
    wins — 0 means exempt; else the clinic default (PRD default Rs. 100).
    The Rs. 200 figure is advice, not a forced cap: clinic config is
    respected even above it (logged, so the clinic can be told)."""
    if clinic is None:
        return 0
    if not getattr(clinic, "booking_amount_enabled", True):
        return 0
    if appointment_type is not None and appointment_type.booking_amount_override is not None:
        amount = int(appointment_type.booking_amount_override)
    else:
        amount = int(clinic.booking_amount_default or 0)
    if amount < 0:
        return 0
    if amount > BOOKING_AMOUNT_ADVICE_MAX:
        logger.warning(
            "Booking amount Rs. %s exceeds PRD advice max Rs. %s for clinic %s — "
            "respecting clinic config (D9)", amount, BOOKING_AMOUNT_ADVICE_MAX,
            getattr(clinic, "id", "?"))
    return amount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_appointment(db: Session, appointment) -> Appointment:
    if isinstance(appointment, Appointment):
        return appointment
    row = db.query(Appointment).filter(Appointment.id == appointment).first()
    if row is None:
        raise ValueError(f"Appointment not found: {appointment}")
    return row


def _clinic_for(db: Session, appointment: Appointment) -> Clinic | None:
    return db.query(Clinic).filter(Clinic.tenant_id == appointment.tenant_id).first()


def _latest_payment(db: Session, appointment_id: str) -> BookingPayment | None:
    return (db.query(BookingPayment)
            .filter(BookingPayment.appointment_id == appointment_id)
            .order_by(BookingPayment.created_at.desc())
            .first())


def _assign_token(db: Session, appointment: Appointment) -> int:
    """Token = count of CONFIRMED appointments for this doctor that day + 1.
    Assign only if missing; never re-assign an existing token."""
    if appointment.token_no is not None:
        return appointment.token_no
    count = db.query(Appointment).filter(
        Appointment.doctor_id == appointment.doctor_id,
        Appointment.date == appointment.date,
        Appointment.status == "CONFIRMED",
        Appointment.id != appointment.id,
    ).count()
    appointment.token_no = count + 1
    return appointment.token_no


def _now_naive():
    return now_ist().replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Payment link (FR-26 flow steps 2-3)
# ---------------------------------------------------------------------------

def _cashfree_order_create(db: Session, amount: int, order_id: str, appointment: Appointment) -> str:
    """Cashfree order-create skeleton (live mode only). Called ONLY when
    Cashfree keys are present — never without keys. Returns the payment
    link from the Cashfree response. Raises on gateway failure so the
    caller never pretends a link exists."""
    import httpx

    base = ("https://sandbox.cashfree.com/pg/orders"
            if settings.cashfree_env.upper() == "SANDBOX"
            else "https://api.cashfree.com/pg/orders")
    patient = db.query(Patient).filter(
        Patient.id == appointment.patient_id,
        Patient.tenant_id == appointment.tenant_id).first()
    payload = {
        "order_id": order_id,
        "order_amount": amount,
        "order_currency": "INR",
        "customer_details": {
            "customer_id": appointment.patient_id,
            "customer_phone": patient.mobile if patient else "",
        },
    }
    resp = httpx.post(
        base,
        headers={
            "x-api-version": "2023-08-01",
            "x-client-id": settings.cashfree_app_id,
            "x-client-secret": settings.cashfree_secret_key,
            "Content-Type": "application/json",
        },
        json=payload, timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    link = data.get("payment_link") or data.get("payment_session_id") and \
        f"{base}/pay/{data.get('payment_session_id')}"
    if not link:
        raise RuntimeError("Cashfree order created but no payment link returned")
    return link


def create_payment_link(db: Session, appointment) -> dict:
    """Create (or reuse) a BookingPayment row status=link_sent with a fresh
    idempotency_key, hold the slot for clinic.hold_minutes, and send the
    payment-link WhatsApp (Template 5) via whatsapp (lazy import).

    Simulated mode (no Cashfree keys): link is
    https://pay.clinic-ai-assistant.local/p/<payment_id>.
    Amount 0 / disabled: no payment needed — returns link=None,
    status="not_required" and does not create a payment row.
    """
    appt = _load_appointment(db, appointment)
    clinic = _clinic_for(db, appt)
    atype = None
    if appt.appointment_type_id:
        atype = db.query(AppointmentType).filter(
            AppointmentType.id == appt.appointment_type_id,
            AppointmentType.tenant_id == appt.tenant_id).first()
    amount = booking_amount_for(clinic, atype)
    if amount <= 0:
        appt.booking_amount = 0
        appt.payment_status = "not_required"
        db.commit()
        return {"link": None, "payment_id": None, "amount": 0, "status": "not_required"}

    existing = _latest_payment(db, appt.id)
    if existing is not None and existing.status == "link_sent" and existing.amount == amount:
        link = f"https://pay.clinic-ai-assistant.local/p/{existing.id}"
        return {"link": link, "payment_id": existing.id, "amount": amount,
                "status": "link_sent", "reused": True}

    hold_minutes = clinic.hold_minutes if clinic else 15
    appt.booking_amount = amount
    appt.payment_status = "pending"
    appt.status = "HELD_PENDING_PAYMENT"
    appt.hold_expires_at = _now_naive() + timedelta(minutes=hold_minutes)

    payment = BookingPayment(
        tenant_id=appt.tenant_id, appointment_id=appt.id, amount=amount,
        gateway=PAYMENT_GATEWAY_NAME, status="link_sent",
        idempotency_key=str(uuid.uuid4()),
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    if settings.simulated_payments:
        link = f"https://pay.clinic-ai-assistant.local/p/{payment.id}"
    else:
        link = _cashfree_order_create(db, amount, payment.id, appt)

    # Send the payment-link WhatsApp (lazy import — avoids a cycle and
    # keeps payments importable without whatsapp configured).
    try:
        from . import whatsapp as _wa
        tenant = db.query(Clinic).filter(Clinic.tenant_id == appt.tenant_id).first()
        _wa.send_payment_link_msg(db, tenant or appt.tenant_id, appt, link, amount)
    except Exception:
        logger.exception("Payment-link WhatsApp failed for appointment %s", appt.id)
    return {"link": link, "payment_id": payment.id, "amount": amount,
            "status": "link_sent"}


def get_payment_status(db: Session, appointment) -> dict:
    """Deterministic payment status for an appointment (agent tool,
    FR-26 data section). Never confirms payment from speech."""
    appt = _load_appointment(db, appointment)
    payment = _latest_payment(db, appt.id)
    return {
        "appointment_id": appt.id, "status": appt.status,
        "payment_status": appt.payment_status, "booking_amount": appt.booking_amount,
        "payment_id": payment.id if payment else None,
        "payment_row_status": payment.status if payment else None,
        "hold_expires_at": appt.hold_expires_at.isoformat() if appt.hold_expires_at else None,
    }


# ---------------------------------------------------------------------------
# Webhook verification + handling (FR-26 flow step 4)
# ---------------------------------------------------------------------------

def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 verification with PAYMENT_WEBHOOK_SECRET when set
    (hex or base64 digest). In simulated mode (no secret) accept ONLY a
    payload field "test_signature": "valid" and ONLY when DEMO_MODE is
    true; otherwise False. A missing/wrong signature never confirms."""
    if settings.payment_webhook_secret:
        digest = hmac.new(settings.payment_webhook_secret.encode(),
                          raw_body, hashlib.sha256)
        candidates = {digest.hexdigest(),
                      __import__("base64").b64encode(digest.digest()).decode()}
        return bool(signature) and any(
            hmac.compare_digest(signature, cand) for cand in candidates)
    if not settings.demo_mode:
        return False
    try:
        payload = json.loads(raw_body.decode() or "{}")
    except Exception:
        return False
    return isinstance(payload, dict) and payload.get("test_signature") == "valid"


def _extract_webhook_fields(payload: dict) -> dict:
    """Accept Cashfree-style nested payloads and our flat simulated shape."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    pay = data.get("payment") if isinstance(data.get("payment"), dict) else {}
    return {
        "order_id": payload.get("order_id") or order.get("order_id") or payload.get("orderId"),
        "gateway_payment_id": (
            payload.get("gateway_payment_id") or payload.get("cf_payment_id")
            or payload.get("payment_id") or pay.get("cf_payment_id") or pay.get("payment_id")
        ),
        "amount": payload.get("amount", payload.get("order_amount", order.get("order_amount"))),
        "status_raw": str(
            payload.get("status") or payload.get("payment_status")
            or pay.get("payment_status") or order.get("order_status")
            or payload.get("type") or data.get("type") or ""
        ).upper(),
        "idempotency_key": payload.get("idempotency_key"),
        "appointment_id": payload.get("appointment_id"),
        "type": str(payload.get("type") or data.get("type") or "").upper(),
    }


def _find_payment(db: Session, fields: dict) -> BookingPayment | None:
    if fields.get("gateway_payment_id"):
        row = db.query(BookingPayment).filter(
            BookingPayment.gateway_payment_id == fields["gateway_payment_id"]).first()
        if row:
            return row
    if fields.get("idempotency_key"):
        row = db.query(BookingPayment).filter(
            BookingPayment.idempotency_key == fields["idempotency_key"]).first()
        if row:
            return row
    if fields.get("order_id"):
        row = db.query(BookingPayment).filter(BookingPayment.id == fields["order_id"]).first()
        if row:
            return row
    if fields.get("appointment_id"):
        return _latest_payment(db, fields["appointment_id"])
    return None


def handle_payment_webhook(db: Session, payload: dict) -> dict:
    """Idempotent + replay-safe webhook handling.

    Lookup by gateway_payment_id or idempotency_key; an already
    paid/refunded payment returns the same result without double-confirming
    (no second token, no second confirmation messages). On success the
    amount must match the BookingPayment row exactly, otherwise the
    webhook is rejected and the appointment stays HELD_PENDING_PAYMENT.
    On failure the appointment stays HELD (slot auto-releases on hold
    expiry via slot_engine.release_expired_holds)."""
    fields = _extract_webhook_fields(payload or {})
    payment = _find_payment(db, fields)
    if payment is None:
        logger.warning("Payment webhook: no matching payment for %s", fields)
        return {"status": "error", "reason": "payment_not_found", "fields": fields}

    if payment.status in ("paid", "adjusted", "refunded", "refund_initiated"):
        appt = _load_appointment(db, payment.appointment_id)
        logger.info("Payment webhook replay ignored payment=%s status=%s",
                    payment.id, payment.status)
        return {"status": "already_processed", "payment_status": payment.status,
                "payment_id": payment.id, "appointment_id": appt.id,
                "appointment_status": appt.status, "token_no": appt.token_no,
                "amount": payment.amount}
    if payment.status == "forfeited":
        return {"status": "error", "reason": "payment_forfeited", "payment_id": payment.id}

    status_raw = fields["status_raw"]
    is_refund = status_raw in _REFUND_STATUSES or "REFUND" in fields["type"]
    is_success = status_raw in _SUCCESS_STATUSES
    is_failure = status_raw in _FAILURE_STATUSES

    if is_refund:
        payment.status = "refunded"
        appt = _load_appointment(db, payment.appointment_id)
        appt.payment_status = "refunded"
        db.commit()
        return {"status": "refunded", "payment_id": payment.id,
                "appointment_id": appt.id, "amount": payment.amount}

    if not is_success:
        # Failure (or an unrecognised status): leave the appointment HELD.
        logger.info("Payment webhook failure payment=%s status=%s", payment.id, status_raw)
        return {"status": "failed", "reason": status_raw or "payment_failed",
                "payment_id": payment.id, "appointment_id": payment.appointment_id}

    # Success: amount must match exactly — wrong amount is rejected.
    if fields.get("amount") is not None:
        try:
            if int(float(fields["amount"])) != int(payment.amount):
                logger.warning("Payment webhook amount mismatch payment=%s expected=%s got=%s",
                               payment.id, payment.amount, fields["amount"])
                return {"status": "rejected", "reason": "amount_mismatch",
                        "payment_id": payment.id, "appointment_id": payment.appointment_id}
        except (TypeError, ValueError):
            return {"status": "rejected", "reason": "amount_mismatch",
                    "payment_id": payment.id, "appointment_id": payment.appointment_id}

    if fields.get("gateway_payment_id"):
        payment.gateway_payment_id = fields["gateway_payment_id"]
    payment.status = "paid"
    appt = _load_appointment(db, payment.appointment_id)
    appt.status = "CONFIRMED"
    appt.payment_status = "paid"
    appt.hold_expires_at = None
    _assign_token(db, appt)
    db.commit()
    logger.info("Payment confirmed appointment=%s token=%s amount=%s",
                appt.id, appt.token_no, payment.amount)

    # FR-11 / FR-12 + Template 6, only now that the gateway verified it.
    try:
        from . import whatsapp as _wa
        tenant = _clinic_for(db, appt) or appt.tenant_id
        _wa.send_payment_received_msg(db, tenant, appt, payment.amount)
        _wa.send_booking_summary(db, tenant, appt)
        _wa.send_patient_confirmation(db, tenant, appt)
    except Exception:
        logger.exception("Post-payment WhatsApp failed for appointment %s", appt.id)
    return {"status": "paid", "payment_id": payment.id, "appointment_id": appt.id,
            "appointment_status": "CONFIRMED", "token_no": appt.token_no,
            "amount": payment.amount}


# ---------------------------------------------------------------------------
# Refunds (FR-26 outcome table)
# ---------------------------------------------------------------------------

def initiate_refund(db: Session, payment_id, reason: str = "timely_cancel") -> dict:
    """Refund a paid BookingPayment. In simulated mode the refund is set
    to refund_initiated, the patient is told it started (Template 7),
    then immediately refunded and told it arrived — two messages, as the
    PRD requires. In live mode only refund_initiated is set here; the
    gateway refund webhook completes it. Already-refunded is idempotent;
    a forfeited payment cannot be refunded."""
    if isinstance(payment_id, BookingPayment):
        payment = payment_id
    else:
        payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
        if payment is None:
            payment = _latest_payment(db, str(payment_id))
    if payment is None:
        return {"status": "error", "reason": "payment_not_found", "payment_id": str(payment_id)}
    if payment.status == "refunded":
        return {"status": "refunded", "already": True, "payment_id": payment.id,
                "amount": payment.amount, "reason": reason}
    if payment.status == "forfeited":
        return {"status": "error", "reason": "payment_forfeited", "payment_id": payment.id}
    if payment.status not in ("paid", "adjusted", "carry_forward"):
        return {"status": "error", "reason": "payment_not_paid", "payment_id": payment.id,
                "payment_row_status": payment.status}

    appt = _load_appointment(db, payment.appointment_id)

    def _send_refund(status_text: str):
        try:
            from . import whatsapp as _wa
            tenant = _clinic_for(db, appt) or appt.tenant_id
            _wa.send_refund_msg(db, tenant, appt, payment.amount,
                                refund_status=status_text, reason_line=reason)
        except Exception:
            logger.exception("Refund WhatsApp failed for payment %s", payment.id)

    payment.status = "refund_initiated"
    appt.payment_status = "refund_initiated"
    db.commit()
    _send_refund("initiated")
    if settings.simulated_payments:
        payment.status = "refunded"
        appt.payment_status = "refunded"
        db.commit()
        _send_refund("completed")
        return {"status": "refunded", "payment_id": payment.id, "amount": payment.amount,
                "reason": reason, "appointment_id": appt.id}
    return {"status": "refund_initiated", "payment_id": payment.id,
            "amount": payment.amount, "reason": reason, "appointment_id": appt.id}


# ---------------------------------------------------------------------------
# Booking outcomes (FR-26 outcome table)
# ---------------------------------------------------------------------------

def _cancel_with_refund(db: Session, appt: Appointment, payment, reason: str) -> dict:
    refund = None
    if payment is not None and payment.status in ("paid", "adjusted", "carry_forward"):
        refund = initiate_refund(db, payment.id, reason)
    elif payment is not None and payment.status == "link_sent":
        # Unpaid hold: nothing was ever taken, so there is nothing to refund.
        refund = {"status": "not_paid", "payment_id": payment.id}
    appt.status = "CANCELLED"
    db.commit()
    return {"status": "cancelled", "appointment_id": appt.id,
            "payment_status": appt.payment_status,
            "refund": refund, "reason": reason}


def apply_booking_outcome(db: Session, appointment_id, outcome: str) -> dict:
    """Apply one FR-26 outcome. ``outcome`` in
    visit_completed | timely_cancel | late_cancel | clinic_cancel |
    no_show | reschedule.

    - visit_completed: amount adjusted against visit fees, COMPLETED.
    - timely_cancel: cancel >= timely_cancel_hours before the slot ->
      full refund, CANCELLED. (If it is in fact late, the clinic's
      late-cancel policy is applied instead and the result says so.)
    - late_cancel: clinic.late_cancel_policy (default forfeit), CANCELLED.
    - clinic_cancel: always full refund, no cut-off condition, CANCELLED.
    - no_show: forfeit ONLY if the appointment was CONFIRMED and a
      reminder was sent (Notification type "reminder") — otherwise refused.
    - reschedule: payment carry_forward, old appointment RESCHEDULED;
      the new booking reuses the payment, no re-pay.
    """
    appt = _load_appointment(db, appointment_id)
    clinic = _clinic_for(db, appt)
    payment = _latest_payment(db, appt.id)
    now = _now_naive()

    if outcome == "visit_completed":
        if payment is not None and payment.status in ("paid", "carry_forward"):
            payment.status = "adjusted"
            appt.payment_status = "adjusted"
        appt.status = "COMPLETED"
        db.commit()
        return {"status": "completed", "appointment_id": appt.id,
                "payment_status": appt.payment_status}

    if outcome == "timely_cancel":
        cut_off = clinic.timely_cancel_hours if clinic else 4
        is_timely = (appt.slot_start - now) >= timedelta(hours=cut_off)
        if not is_timely:
            logger.info("timely_cancel called late for %s — applying late-cancel policy", appt.id)
            return apply_booking_outcome(db, appt.id, "late_cancel")
        result = _cancel_with_refund(db, appt, payment, "timely_cancel")
        result["outcome"] = "timely_cancel"
        return result

    if outcome == "late_cancel":
        policy = clinic.late_cancel_policy if clinic else "forfeit"
        if policy == "full_refund":
            result = _cancel_with_refund(db, appt, payment, "late_cancel")
            result["outcome"] = "late_cancel"
            result["policy"] = policy
            return result
        if policy == "partial_refund" and payment is not None and payment.status in ("paid", "adjusted"):
            half = payment.amount // 2
            payment.status = "refunded"
            appt.payment_status = "refunded"
            appt.status = "CANCELLED"
            db.commit()
            try:
                from . import whatsapp as _wa
                tenant = clinic or appt.tenant_id
                _wa.send_refund_msg(db, tenant, appt, half,
                                    refund_status="completed", reason_line="late_cancel_partial")
            except Exception:
                logger.exception("Partial-refund WhatsApp failed for %s", appt.id)
            return {"status": "cancelled", "outcome": "late_cancel", "policy": policy,
                    "refund_amount": half, "appointment_id": appt.id,
                    "payment_status": appt.payment_status}
        # Default: forfeit.
        if payment is not None and payment.status in ("paid", "adjusted", "carry_forward"):
            payment.status = "forfeited"
            appt.payment_status = "forfeited"
        appt.status = "CANCELLED"
        db.commit()
        return {"status": "cancelled", "outcome": "late_cancel", "policy": policy,
                "appointment_id": appt.id, "payment_status": appt.payment_status}

    if outcome == "clinic_cancel":
        result = _cancel_with_refund(db, appt, payment, "clinic_cancel")
        result["outcome"] = "clinic_cancel"
        return result

    if outcome == "no_show":
        # FR-26 no-show rule: forfeit ONLY when CONFIRMED + reminder sent.
        # A reminder-cancel (timely) is never a forfeit — that path goes
        # through timely_cancel, not here.
        if appt.status != "CONFIRMED":
            return {"status": "error", "reason": "no_show_requires_confirmed",
                    "appointment_id": appt.id, "appointment_status": appt.status}
        patient = db.query(Patient).filter(
            Patient.id == appt.patient_id, Patient.tenant_id == appt.tenant_id).first()
        reminder_sent = False
        if patient:
            reminder_sent = db.query(Notification).filter(
                Notification.tenant_id == appt.tenant_id,
                Notification.type == "reminder",
                Notification.to_number == patient.mobile,
            ).first() is not None
        if not reminder_sent:
            return {"status": "error", "reason": "no_show_requires_reminder_sent",
                    "appointment_id": appt.id}
        if payment is not None and payment.status in ("paid", "adjusted", "carry_forward"):
            payment.status = "forfeited"
            appt.payment_status = "forfeited"
        appt.status = "NO_SHOW"
        if patient is not None:
            patient.no_show_count = (patient.no_show_count or 0) + 1
        db.commit()
        return {"status": "no_show", "appointment_id": appt.id,
                "payment_status": appt.payment_status}

    if outcome == "reschedule":
        if payment is not None and payment.status in ("paid", "adjusted"):
            payment.status = "carry_forward"
            appt.payment_status = "carry_forward"
        appt.status = "RESCHEDULED"
        db.commit()
        return {"status": "rescheduled", "appointment_id": appt.id,
                "payment_status": appt.payment_status,
                "note": "Payment carry-forward — new booking reuses it, no re-pay."}

    return {"status": "error", "reason": "unknown_outcome", "outcome": outcome}


def manual_confirm(db: Session, appointment_id, by: str = "reception") -> dict:
    """No-UPI exception (FR-26 flow step 6): reception manually confirms
    a held booking for a patient who cannot pay via UPI (elderly, no
    smartphone). This is an explicit human reception action — it is NOT
    patient speech, and it never claims a payment was made: the payment
    status is set to not_required, not paid. A token is assigned and the
    patient confirmation + booking summary are sent."""
    appt = _load_appointment(db, appointment_id)
    if appt.status == "CONFIRMED":
        return {"status": "already_confirmed", "appointment_id": appt.id,
                "token_no": appt.token_no}
    if appt.status not in ("HELD_PENDING_PAYMENT",):
        return {"status": "error", "reason": "cannot_manual_confirm",
                "appointment_id": appt.id, "appointment_status": appt.status}
    appt.status = "CONFIRMED"
    appt.payment_status = "not_required"
    appt.hold_expires_at = None
    _assign_token(db, appt)
    db.commit()
    logger.info("Manual confirm (no-UPI exception) appointment=%s by=%s token=%s",
                appt.id, by, appt.token_no)
    try:
        from . import whatsapp as _wa
        tenant = _clinic_for(db, appt) or appt.tenant_id
        _wa.send_booking_summary(db, tenant, appt)
        _wa.send_patient_confirmation(db, tenant, appt)
    except Exception:
        logger.exception("Manual-confirm WhatsApp failed for %s", appt.id)
    return {"status": "confirmed", "manual": True, "by": by,
            "appointment_id": appt.id, "token_no": appt.token_no}
