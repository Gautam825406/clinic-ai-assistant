"""Payments router — FR-26 booking amount (PRD Sec 6).

POST /payments/link and POST /webhooks/payment delegate to the payments
module when it exists; until then a deterministic simulated fallback runs
so the demo flow works with zero credentials. The gateway name is never
shown to patients (CONTRACT) — links and messages carry only the clinic name.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Appointment, BookingPayment, User
from ..schemas import PaymentLinkRequest, RefundRequest
from ..security import get_current_user
from .common import appointment_for
from .booking import _booking_amount

router = APIRouter(tags=["payments"])


def _simulated_link(db: Session, appt: Appointment) -> dict:
    amount = appt.booking_amount or _booking_amount(db, appt.tenant_id, appt.appointment_type_id)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Is booking par booking amount nahi hai")
    existing = db.query(BookingPayment).filter(
        BookingPayment.appointment_id == appt.id,
        BookingPayment.status == "link_sent").first()
    if existing is not None:
        return {"link": f"https://pay.demo/clinic-ai-assistant/{existing.id}",
                "payment_id": existing.id, "amount": existing.amount,
                "status": existing.status}
    payment = BookingPayment(
        tenant_id=appt.tenant_id, appointment_id=appt.id, amount=amount,
        gateway="cashfree", status="link_sent",
        idempotency_key=f"link-{appt.id}",
    )
    db.add(payment)
    appt.booking_amount = amount
    appt.payment_status = "pending"
    db.commit()
    db.refresh(payment)
    return {"link": f"https://pay.demo/clinic-ai-assistant/{payment.id}",
            "payment_id": payment.id, "amount": amount, "status": "link_sent"}


@router.post("/payments/link")
def create_link(body: PaymentLinkRequest, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    appt = appointment_for(db, user.tenant_id, body.appointment_id)
    try:
        from ..payments import create_payment_link
    except Exception:
        create_payment_link = None
    if create_payment_link is not None:
        return create_payment_link(db, appt)
    return _simulated_link(db, appt)


@router.post("/webhooks/payment")
async def payment_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    try:
        from ..payments import handle_payment_webhook
    except Exception:
        handle_payment_webhook = None
    if handle_payment_webhook is not None:
        return handle_payment_webhook(db, payload)
    # Simulated fallback: idempotent on gateway_payment_id.
    payment_id = payload.get("payment_id") or payload.get("booking_payment_id")
    gateway_payment_id = payload.get("gateway_payment_id")
    status = (payload.get("status") or "").lower()
    payment = None
    if gateway_payment_id:
        payment = db.query(BookingPayment).filter(
            BookingPayment.gateway_payment_id == gateway_payment_id).first()
    if payment is None and payment_id:
        payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status == "paid" and status in ("paid", "success"):
        appt = db.query(Appointment).filter(Appointment.id == payment.appointment_id).first()
        return {"status": "ok", "duplicate": True,
                "appointment_id": payment.appointment_id,
                "appointment_status": appt.status if appt else None}
    if status in ("paid", "success"):
        payment.status = "paid"
        payment.gateway_payment_id = gateway_payment_id or payment.gateway_payment_id
        appt = db.query(Appointment).filter(Appointment.id == payment.appointment_id).first()
        if appt is not None:
            appt.status = "CONFIRMED"
            appt.payment_status = "paid"
            if appt.token_no is None:
                day_count = db.query(Appointment).filter(
                    Appointment.doctor_id == appt.doctor_id, Appointment.date == appt.date,
                    Appointment.status.in_(("CONFIRMED", "HELD_PENDING_PAYMENT"))).count()
                appt.token_no = day_count
        db.commit()
        return {"status": "ok", "appointment_id": payment.appointment_id,
                "appointment_status": "CONFIRMED"}
    if status in ("failed", "failure"):
        payment.status = "link_sent"
        db.commit()
        return {"status": "ok", "appointment_id": payment.appointment_id,
                "appointment_status": "payment_failed"}
    raise HTTPException(status_code=400, detail=f"Unknown payment status: {status!r}")


@router.post("/payments/{payment_id}/refund")
def refund_payment(payment_id: str, body: RefundRequest,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment = db.query(BookingPayment).filter(BookingPayment.id == payment_id).first()
    if payment is None or payment.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="Payment not found")
    try:
        from ..payments import initiate_refund
    except Exception:
        initiate_refund = None
    if initiate_refund is not None:
        return initiate_refund(db, payment_id, body.reason)
    payment.status = "refund_initiated"
    appt = db.query(Appointment).filter(Appointment.id == payment.appointment_id).first()
    if appt is not None:
        appt.payment_status = "refund_initiated"
    db.commit()
    return {"payment_id": payment.id, "status": "refund_initiated",
            "amount": payment.amount, "reason": body.reason}
