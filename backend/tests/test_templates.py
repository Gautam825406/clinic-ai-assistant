"""Template tests — PRD Sec 6 specialty templates, pilot General first."""
import pytest

from app.specialty_templates import TEMPLATES, apply_template_to_tenant, get_template


def test_all_six_templates_present():
    assert set(TEMPLATES) == {
        "general", "dental", "dermatology", "paediatrics", "physiotherapy", "custom",
    }


def test_general_is_pilot_complete():
    general = TEMPLATES["general"]
    assert general["name"] == "General / GP"
    names = [t["name"] for t in general["appointment_types"]]
    assert names == ["New Consult", "Follow-up", "Report dikhana"]
    durations = {t["name"]: t["duration_minutes"] for t in general["appointment_types"]}
    assert durations == {"New Consult": 15, "Follow-up": 10, "Report dikhana": 10}
    follow_up = [t for t in general["appointment_types"] if t["name"] == "Follow-up"][0]
    assert follow_up["booking_amount_override"] == 0  # follow-up exempt (FR-26)
    assert general["intake_questions"] == [
        "Problem kab se hai?", "Pehle visit hua tha?",
    ]
    assert len(general["intake_questions"]) == 2
    assert general["summary_fields"] == ["reason_line", "new_or_followup"]


def test_no_cross_template_leak():
    general_qs = set(TEMPLATES["general"]["intake_questions"])
    dental_qs = set(TEMPLATES["dental"]["intake_questions"])
    assert not (general_qs & dental_qs)
    assert "Dard hai ya routine check-up?" in dental_qs
    assert "Dard hai ya routine check-up?" not in general_qs
    for code, tpl in TEMPLATES.items():
        assert len(tpl["intake_questions"]) <= 3, code
        assert all("name" in t and "duration_minutes" in t for t in tpl["appointment_types"])


def test_other_templates_shape():
    dental = TEMPLATES["dental"]
    assert [t["name"] for t in dental["appointment_types"]] == [
        "Check-up / Cleaning", "Dard consult", "RCT / Procedure"]
    rct = [t for t in dental["appointment_types"] if t["name"] == "RCT / Procedure"][0]
    assert rct["booking_amount_override"] == 100
    assert "Vaccination" in [t["name"] for t in TEMPLATES["paediatrics"]["appointment_types"]]
    assert any("age" in q.lower() for q in TEMPLATES["paediatrics"]["intake_questions"])
    assert TEMPLATES["custom"]["appointment_types"] == []
    assert TEMPLATES["custom"]["intake_questions"] == []


def test_get_template_returns_copy_and_rejects_unknown():
    tpl = get_template("general")
    tpl["intake_questions"].append("MUTATED?")
    assert "MUTATED?" not in get_template("general")["intake_questions"]
    with pytest.raises(KeyError):
        get_template("cardiology")


def test_apply_template_idempotent():
    from app.database import SessionLocal, init_db
    from app.models import Tenant

    init_db()
    db = SessionLocal()
    try:
        tenant = Tenant(name="Template Test Clinic", type="clinic")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        first = apply_template_to_tenant(db, tenant.id, "general")
        second = apply_template_to_tenant(db, tenant.id, "general")
        assert len(first) == 3
        assert len(second) == 3
        assert {t.id for t in first} == {t.id for t in second}
        assert {t.name for t in second} == {"New Consult", "Follow-up", "Report dikhana"}
    finally:
        db.close()
