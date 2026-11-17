"""Specialty templates — PRD Section 6 (specialty templates) + FR-26.

A template is config only: appointment types (name + duration + fees text
+ prep line + booking-amount override), at most 3 intake questions, and
the summary fields the doctor sees. No medical advice lives here — intake
questions are for visit preparation only, never diagnosis.

Pilot specialty is General / GP (task brief): it is the default template
applied at signup and used by the demo seed. Other templates are built as
config-only; they are not the pilot.
"""
import copy

from sqlalchemy.orm import Session

TEMPLATES: dict[str, dict] = {
    "general": {
        "name": "General / GP",
        "appointment_types": [
            {
                "name": "New Consult",
                "duration_minutes": 15,
                "fees_text": "Consultation Rs. 500 (range, confirm at reception)",
                "prep_line": "Apni purani reports / dawaai ki list saath laayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Follow-up",
                "duration_minutes": 10,
                "fees_text": "Follow-up fees reception par confirm karein",
                "prep_line": "Pehli visit ki reports saath laayein.",
                "booking_amount_override": 0,
            },
            {
                "name": "Report dikhana",
                "duration_minutes": 10,
                "fees_text": "Report dikhana — fees reception par confirm karein",
                "prep_line": "Saari reports saath laayein.",
                "booking_amount_override": None,
            },
        ],
        "intake_questions": [
            "Problem kab se hai?",
            "Pehle visit hua tha?",
        ],
        "summary_fields": ["reason_line", "new_or_followup"],
    },
    "dental": {
        "name": "Dental",
        "appointment_types": [
            {
                "name": "Check-up / Cleaning",
                "duration_minutes": 30,
                "fees_text": "Check-up / Cleaning — fees reception par confirm karein",
                "prep_line": "Subah brush karke aayein; khaana khaakar aayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Dard consult",
                "duration_minutes": 15,
                "fees_text": "Dard consult — fees reception par confirm karein",
                "prep_line": "Dard wali side aur kab se hai, ye bataayein.",
                "booking_amount_override": None,
            },
            {
                "name": "RCT / Procedure",
                "duration_minutes": 30,
                "fees_text": "Procedure fees doctor ke hisaab se — reception par confirm karein",
                "prep_line": "Doctor procedure se pehle X-ray / check-up kar sakte hain.",
                "booking_amount_override": 100,
            },
        ],
        "intake_questions": [
            "Dard hai ya routine check-up?",
            "Kis side / kab se?",
        ],
        "summary_fields": ["reason_line", "visit_type", "procedure_flag"],
    },
    "dermatology": {
        "name": "Dermatology",
        "appointment_types": [
            {
                "name": "Skin consult",
                "duration_minutes": 15,
                "fees_text": "Skin consult — fees reception par confirm karein",
                "prep_line": "Us jagah par koi cream / dawaai lagakar na aayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Follow-up",
                "duration_minutes": 10,
                "fees_text": "Follow-up fees reception par confirm karein",
                "prep_line": "Pehli visit ki reports aur dawaai saath laayein.",
                "booking_amount_override": 0,
            },
            {
                "name": "Procedure enquiry",
                "duration_minutes": 15,
                "fees_text": "Procedure enquiry — callback par fees batayi jayegi",
                "prep_line": "Reception callback par procedure ki jaankari dega.",
                "booking_amount_override": 0,
            },
        ],
        "intake_questions": [
            "Problem kis jagah / kab se hai?",
            "Pehle treatment liya?",
        ],
        "summary_fields": ["reason_line", "body_site", "new_or_followup"],
    },
    "paediatrics": {
        "name": "Paediatrics",
        "appointment_types": [
            {
                "name": "Child consult",
                "duration_minutes": 15,
                "fees_text": "Child consult — fees reception par confirm karein",
                "prep_line": "Bacche ki purani reports / vaccination card saath laayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Vaccination",
                "duration_minutes": 10,
                "fees_text": "Vaccination — fees reception par confirm karein",
                "prep_line": "Vaccination card saath laayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Follow-up",
                "duration_minutes": 10,
                "fees_text": "Follow-up fees reception par confirm karein",
                "prep_line": "Pehli visit ki reports saath laayein.",
                "booking_amount_override": 0,
            },
        ],
        "intake_questions": [
            "Bacche ki umar kitni hai? (age mandatory)",
            "Problem kab se hai?",
            "Pehle visit hua tha?",
        ],
        "summary_fields": ["reason_line", "child_age", "new_or_followup"],
    },
    "physiotherapy": {
        "name": "Physiotherapy",
        "appointment_types": [
            {
                "name": "Assessment",
                "duration_minutes": 30,
                "fees_text": "Assessment — fees reception par confirm karein",
                "prep_line": "Aaramdayak kapde pehnein; purani reports saath laayein.",
                "booking_amount_override": None,
            },
            {
                "name": "Session",
                "duration_minutes": 30,
                "fees_text": "Session fees doctor ke hisaab se — reception par confirm karein",
                "prep_line": "Aaramdayak kapde pehnein.",
                "booking_amount_override": None,
            },
            {
                "name": "Package enquiry",
                "duration_minutes": 15,
                "fees_text": "Package enquiry — callback par fees batayi jayegi",
                "prep_line": "Reception callback par package ki jaankari dega.",
                "booking_amount_override": 0,
            },
        ],
        "intake_questions": [
            "Dard / problem kis jagah / kab se hai?",
            "Pehle physiotherapy li thi?",
        ],
        "summary_fields": ["reason_line", "body_site", "new_or_followup"],
    },
    "custom": {
        "name": "Custom",
        "appointment_types": [],
        "intake_questions": [],
        "summary_fields": ["reason_line"],
    },
}


def get_template(code: str) -> dict:
    """Return a deep copy of the template for `code` (never the live dict,
    so callers cannot mutate the library). Raises KeyError for unknown codes."""
    if code not in TEMPLATES:
        raise KeyError(f"unknown specialty template: {code!r}")
    return copy.deepcopy(TEMPLATES[code])


def apply_template_to_tenant(db: Session, tenant_id: str, code: str) -> list:
    """Create AppointmentType rows for `tenant_id` from template `code`.

    Idempotent: appointment types whose name already exists for this tenant
    are skipped (and returned as-is). Also upserts the tenant's
    SpecialtyTemplate row. Returns the full list of AppointmentType rows
    for the template (new + pre-existing).
    """
    from .models import AppointmentType, SpecialtyTemplate

    template = get_template(code)
    existing_template = (
        db.query(SpecialtyTemplate)
        .filter(SpecialtyTemplate.tenant_id == tenant_id, SpecialtyTemplate.code == code)
        .first()
    )
    if existing_template is None:
        db.add(
            SpecialtyTemplate(
                tenant_id=tenant_id,
                code=code,
                name=template["name"],
                intake_questions=template["intake_questions"],
                summary_fields=template["summary_fields"],
                active=True,
            )
        )
    else:
        existing_template.name = template["name"]
        existing_template.intake_questions = template["intake_questions"]
        existing_template.summary_fields = template["summary_fields"]
    db.commit()

    out = []
    for atype in template["appointment_types"]:
        existing = (
            db.query(AppointmentType)
            .filter(
                AppointmentType.tenant_id == tenant_id,
                AppointmentType.name == atype["name"],
            )
            .first()
        )
        if existing is not None:
            out.append(existing)
            continue
        row = AppointmentType(
            tenant_id=tenant_id,
            template_id=code,
            name=atype["name"],
            duration_minutes=atype["duration_minutes"],
            fees_text=atype["fees_text"],
            prep_line=atype["prep_line"],
            booking_amount_override=atype["booking_amount_override"],
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        out.append(row)
    return out
