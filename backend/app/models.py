"""Data model — PRD Section 10. tenant_id is mandatory on EVERY table.
Cross-tenant leak = product over, so every query must filter by tenant_id
(Section 10.2) and tests prove Clinic A can never read Clinic B rows.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, ForeignKey, Integer, String,
    Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .database import Base


def _id():
    return str(uuid.uuid4())


def _now():
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(String, primary_key=True, default=_id)
    name = Column(String, nullable=False)
    type = Column(String, default="clinic")  # clinic | coaching | college
    city = Column(String, default="Bangalore")
    plan = Column(String, default="pilot")  # pilot | starter | growth | clinic_plus
    status = Column(String, default="trial")  # trial | active | past_due | paused | cancelled
    trial_ends_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    mobile = Column(String, nullable=False)
    email = Column(String, nullable=True)
    role = Column(String, default="owner")  # owner | doctor | reception | platform_admin
    password_hash = Column(String, nullable=False, default="")
    api_token = Column(String, unique=True, nullable=True)
    is_platform_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_now)


class Clinic(Base):
    __tablename__ = "clinics"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False, unique=True)
    display_name = Column(String, nullable=False)
    specialty_template_id = Column(String, default="general")
    address = Column(Text, default="")
    google_maps_text = Column(Text, default="")
    fees_text = Column(Text, default="")
    greeting_text = Column(Text, default="")
    languages = Column(String, default="hinglish")
    whatsapp_doctor = Column(String, default="")
    whatsapp_reception = Column(String, default="")
    telephony_number = Column(String, default="")
    opening_time = Column(String, default="09:00")
    closing_time = Column(String, default="21:00")
    # FR-26 booking amount (v1.1+): clinic decides, never fixed centrally.
    booking_amount_enabled = Column(Boolean, default=True)
    booking_amount_default = Column(Integer, default=100)  # Rs, PRD default advice
    timely_cancel_hours = Column(Integer, default=4)
    late_cancel_policy = Column(String, default="forfeit")  # forfeit | partial_refund | full_refund
    hold_minutes = Column(Integer, default=15)  # payment hold window, config 10-30
    reminder_hours_before = Column(Integer, default=2)
    no_show_grace_minutes = Column(Integer, default=15)
    created_at = Column(DateTime, default=_now)


class Doctor(Base):
    __tablename__ = "doctors"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    speciality = Column(String, default="")
    slot_minutes = Column(Integer, default=15)
    active = Column(Boolean, default=True)


class SpecialtyTemplate(Base):
    __tablename__ = "specialty_templates"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=True)  # NULL = library template
    code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    intake_questions = Column(JSON, default=list)
    summary_fields = Column(JSON, default=list)
    active = Column(Boolean, default=True)


class AppointmentType(Base):
    __tablename__ = "appointment_types"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    template_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    duration_minutes = Column(Integer, default=15)
    fees_text = Column(String, default="")
    prep_line = Column(String, default="")
    booking_amount_override = Column(Integer, nullable=True)  # None = clinic default; 0 = exempt


class Schedule(Base):
    __tablename__ = "schedules"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    doctor_id = Column(String, ForeignKey("doctors.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0 = Monday
    start_time = Column(String, nullable=False)  # "09:00"
    end_time = Column(String, nullable=False)
    break_start = Column(String, nullable=True)
    break_end = Column(String, nullable=True)


class Holiday(Base):
    __tablename__ = "holidays"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    doctor_id = Column(String, ForeignKey("doctors.id"), nullable=True)
    date = Column(Date, nullable=False)
    reason = Column(String, default="")


class Patient(Base):
    __tablename__ = "patients"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, nullable=False)
    mobile = Column(String, nullable=False, index=True)
    language_pref = Column(String, default="hinglish")
    consent_flags = Column(JSON, default=dict)
    no_show_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


APPOINTMENT_STATUSES = (
    "HELD_PENDING_PAYMENT", "CONFIRMED", "CANCELLED",
    "RESCHEDULED", "COMPLETED", "NO_SHOW",
)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (UniqueConstraint("doctor_id", "slot_start", name="uq_doctor_slot"),)
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    doctor_id = Column(String, ForeignKey("doctors.id"), nullable=False)
    patient_id = Column(String, ForeignKey("patients.id"), nullable=False)
    appointment_type_id = Column(String, ForeignKey("appointment_types.id"), nullable=True)
    date = Column(Date, nullable=False)
    slot_start = Column(DateTime, nullable=False)
    slot_end = Column(DateTime, nullable=False)
    reason_line = Column(String, default="")
    status = Column(String, default="CONFIRMED")
    source = Column(String, default="voice")  # voice | manual | whatsapp
    token_no = Column(Integer, nullable=True)
    payment_status = Column(String, default="not_required")  # not_required|pending|paid|adjusted|carry_forward|refund_initiated|refunded|forfeited
    booking_amount = Column(Integer, default=0)
    hold_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)


class Call(Base):
    __tablename__ = "calls"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    call_sid = Column(String, nullable=False)
    direction = Column(String, default="inbound")
    from_number = Column(String, default="")
    started_at = Column(DateTime, default=_now)
    duration_sec = Column(Integer, default=0)
    outcome = Column(String, default="unknown")
    language_detected = Column(String, default="hinglish")
    recording_url = Column(String, nullable=True)
    transcript = Column(JSON, default=list)
    summary_sent_at = Column(DateTime, nullable=True)
    failure_reason = Column(String, nullable=True)
    consent_line_played = Column(Boolean, default=False)


class CallbackTicket(Base):
    __tablename__ = "callback_tickets"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    name = Column(String, default="")
    mobile = Column(String, nullable=False)
    reason = Column(String, default="")
    priority = Column(String, default="normal")
    status = Column(String, default="open")  # open | done
    created_at = Column(DateTime, default=_now)


class Subscription(Base):
    __tablename__ = "subscriptions"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    plan = Column(String, default="pilot")
    status = Column(String, default="trial")
    current_period_start = Column(DateTime, nullable=True)
    current_period_end = Column(DateTime, nullable=True)
    autopay_mandate_id = Column(String, nullable=True)  # mandate id only, never card/UPI details
    amount = Column(Integer, default=0)  # paise? No — rupees, per PRD pricing
    gst_invoice_no = Column(String, nullable=True)


PAYMENT_STATUSES = (
    "link_sent", "paid", "adjusted", "carry_forward",
    "refund_initiated", "refunded", "forfeited",
)


class BookingPayment(Base):
    __tablename__ = "booking_payments"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    appointment_id = Column(String, ForeignKey("appointments.id"), nullable=False)
    amount = Column(Integer, nullable=False)  # rupees
    gateway = Column(String, default="cashfree")
    gateway_payment_id = Column(String, nullable=True)
    status = Column(String, default="link_sent")
    idempotency_key = Column(String, unique=True, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    type = Column(String, nullable=False)
    to_number = Column(String, nullable=False)
    template_name = Column(String, nullable=False)
    status = Column(String, default="queued")  # queued | sent | delivered | failed
    provider_msg_id = Column(String, nullable=True)
    body = Column(Text, default="")
    sent_at = Column(DateTime, nullable=True)


class ClinicNumber(Base):
    """v1.1+ addition (user, 17 Nov evening): EVERY clinic has its OWN
    phone/WhatsApp number, verified via OTP in its dashboard. Never one
    shared number across clinics."""
    __tablename__ = "clinic_numbers"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    phone_number = Column(String, nullable=False)
    purpose = Column(String, default="voice")  # voice | whatsapp | both
    verified = Column(Boolean, default=False)
    otp_hash = Column(String, nullable=True)
    otp_expires_at = Column(DateTime, nullable=True)
    otp_attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(String, primary_key=True, default=_id)
    tenant_id = Column(String, ForeignKey("tenants.id"), nullable=False)
    actor = Column(String, default="")
    action = Column(String, nullable=False)
    entity = Column(String, default="")
    entity_id = Column(String, default="")
    detail = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now)
