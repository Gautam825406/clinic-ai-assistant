"""Plans — PRD Sec 13.2 (SaaS pricing hypothesis), config-only.

No billing is live here: there is no payment collection, no autopay
mandate creation and no invoice generation in code. These plan
definitions exist so the landing page, dashboard and subscription
endpoint all describe the SAME plans, and so a future billing step
(Cashfree prod keys + pilot per-call cost validation) has a hook to
attach to. Prices are the PRD's indicative bands — a target /
hypothesis, NOT a final locked price. PRD Sec 13.2/13.8: validate the
real per-call cost from the pilot's first 7 days before locking any
price; if cost is higher, adjust the plan's call limit / included
minutes first, not the price on hope.
"""

PLANS = {
    "pilot": {
        "id": "pilot",
        "name": "Pilot",
        "price_rs_per_month": 0,
        "price_note": "Pehli 1-3 clinics — 7-14 din free / nominal, feedback ke badle",
        "price_status": "hypothesis",
        "doctors": "1 doctor, 1 number",
        "calls_per_month": "trial period",
        "features": [
            "Hinglish voice booking (call → slot → confirm-back)",
            "Refundable booking amount (FR-26) — clinic-configurable",
            "WhatsApp booking summary + patient confirmation",
            "Daily digest (doctor + reception)",
            "Call log + callback queue",
            "1 specialty template (General / GP pilot)",
        ],
    },
    "starter": {
        "id": "starter",
        "name": "Starter",
        "price_rs_per_month": 2999,
        "price_note": "Target price — per-call cost validate hone se pehle lock nahi hoga",
        "price_status": "hypothesis",
        "doctors": "1-2 doctors, 1 branch",
        "calls_per_month": "up to ~600 calls/mo",
        "features": [
            "Pilot ke saare features",
            "1 number, apna workspace, apna login",
            "Booking + WhatsApp summary, daily digest, call logs",
            "1 specialty template",
        ],
    },
    "growth": {
        "id": "growth",
        "name": "Growth",
        "price_rs_per_month": 5999,
        "price_note": "Indicative band — pilot cost validation ke baad final hoga",
        "price_status": "hypothesis",
        "doctors": "3-5 doctors",
        "calls_per_month": "up to ~1,500 calls/mo",
        "features": [
            "Starter ke saare features",
            "Appointment reminders (FR-14) + returning-patient recognition",
            "Multi-doctor routing, callback queue, manual / walk-in entry",
            "Saare specialty templates",
        ],
    },
    "clinic_plus": {
        "id": "clinic_plus",
        "name": "Clinic+",
        "price_rs_per_month": 9999,
        "price_note": "Rs. 9,999/mo se — indicative band, final nahi",
        "price_status": "hypothesis",
        "doctors": "6+ doctors / multi-branch",
        "calls_per_month": "high volume",
        "features": [
            "Growth ke saare features",
            "Branch-wise workspace, reports, priority support",
            "Custom greeting / voice",
        ],
    },
}


def get_plan(plan_id: str) -> dict:
    """Return a copy of a plan, or the Pilot plan for unknown ids."""
    plan = PLANS.get(plan_id) or PLANS["pilot"]
    return {**plan, "features": list(plan["features"])}
