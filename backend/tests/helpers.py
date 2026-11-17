"""Shared seeding helpers for the dialogue / telephony tests."""
from app.models import Clinic, Doctor, Schedule, Tenant


def seed_clinic(db, booking_amount_enabled=False, fees_text="",
                greeting_text="", telephony_number="", address=""):
    """One tenant + clinic + doctor (Dr. Sharma, General/GP) with
    all-weekday schedules 09:00-13:00 + 16:00-21:00. Returns
    (tenant, clinic, doctor)."""
    tenant = Tenant(name="Test Clinic Tenant", type="clinic", city="Bangalore")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    clinic = Clinic(
        tenant_id=tenant.id, display_name="Care Plus Clinic",
        specialty_template_id="general", address=address,
        fees_text=fees_text, greeting_text=greeting_text,
        opening_time="09:00", closing_time="21:00",
        telephony_number=telephony_number,
        booking_amount_enabled=booking_amount_enabled,
        booking_amount_default=100, timely_cancel_hours=4,
        late_cancel_policy="forfeit", hold_minutes=15,
    )
    db.add(clinic)
    db.commit()
    db.refresh(clinic)
    doctor = Doctor(tenant_id=tenant.id, name="Dr. Sharma",
                    speciality="General", slot_minutes=15, active=True)
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    for weekday in range(7):
        for start, end in (("09:00", "13:00"), ("16:00", "21:00")):
            db.add(Schedule(tenant_id=tenant.id, doctor_id=doctor.id,
                            weekday=weekday, start_time=start, end_time=end))
    db.commit()
    return tenant, clinic, doctor
