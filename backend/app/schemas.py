"""Pydantic schemas — API contracts. BookingExtraction is Appendix B of the PRD."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    clinic_name: str
    owner_name: str
    owner_mobile: str
    password: str
    city: str = "Bangalore"
    specialty: str = "general"


class LoginRequest(BaseModel):
    mobile: str
    password: str


class DoctorCreate(BaseModel):
    name: str
    speciality: str = ""
    slot_minutes: int = 15


class ScheduleCreate(BaseModel):
    doctor_id: str
    weekday: int = Field(ge=0, le=6)
    start_time: str
    end_time: str
    break_start: str | None = None
    break_end: str | None = None


class ClinicConfigUpdate(BaseModel):
    display_name: str | None = None
    specialty_template_id: str | None = None
    address: str | None = None
    fees_text: str | None = None
    greeting_text: str | None = None
    whatsapp_doctor: str | None = None
    whatsapp_reception: str | None = None
    opening_time: str | None = None
    closing_time: str | None = None
    booking_amount_enabled: bool | None = None
    booking_amount_default: int | None = None
    timely_cancel_hours: int | None = None
    late_cancel_policy: str | None = None
    hold_minutes: int | None = None
    reminder_hours_before: int | None = None


class AppointmentCreate(BaseModel):
    doctor_id: str
    patient_name: str
    patient_mobile: str
    date: date
    slot_start: datetime
    reason_line: str = ""
    appointment_type_id: str | None = None
    source: str = "manual"


class BookingExtraction(BaseModel):
    """Appendix B — LLM output only. Free text never goes straight to the DB."""
    intent: Literal["book", "reschedule", "cancel", "faq", "human", "emergency", "unknown"]
    patient_name: str | None = None
    mobile: str | None = None
    doctor_hint: str | None = None
    date_hint: str | None = None
    time_hint: str | None = None
    reason_line: str | None = None  # max 8 words, never a diagnosis
    confidence: float = 0.0


class OTPRequest(BaseModel):
    phone_number: str
    purpose: str = "voice"


class OTPVerify(BaseModel):
    phone_number: str
    otp: str


class PaymentLinkRequest(BaseModel):
    appointment_id: str


class RefundRequest(BaseModel):
    reason: str = "timely_cancel"
