"""Demo call — end-to-end simulated flow for Clinic AI Assistant.

Run from the repo root:  .venv/bin/python scripts/demo_call.py

Starts from a FRESH database (data/demo_call.db is deleted first), seeds
the DEMO clinic, runs a full simulated voice call through the real
DialogueEngine turn-by-turn, creates the FR-26 payment link, simulates the
gateway webhook, and prints the WhatsApp outbox + daily digest.

Everything here is simulated (no real calls, no real payments, no real
WhatsApp) — the demo patient, clinic and doctor are all flagged DEMO.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

DB_PATH = ROOT / "data" / "demo_call.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["DEMO_MODE"] = "true"
os.environ["SEED_DEMO"] = "0"

from app.database import SessionLocal, init_db  # noqa: E402
from app.date_parser import now_ist  # noqa: E402
from app.dialogue import DialogueEngine  # noqa: E402
from app.models import Appointment, Clinic, Notification  # noqa: E402
from app.notifications import build_daily_digest  # noqa: E402
from app.payments import create_payment_link, handle_payment_webhook  # noqa: E402
from app.seed import delete_demo, seed_demo  # noqa: E402
from app import whatsapp as wa  # noqa: E402

TURNS = [
    "Namaste",
    "Haan",
    "Mujhe kal shaam Dr. Sharma ko dikhana hai, bukhaar hai",
    "Mera naam Rahul Verma hai, mobile 9000000001",
    "shaam 5 baje",
    "Haan, confirm karein",
]


def step(n: int, title: str) -> None:
    print(f"\n--- Step {n}: {title} ---")


def main() -> int:
    if DB_PATH.exists():
        DB_PATH.unlink()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    db = SessionLocal()
    try:
        step(1, "Fresh DB + DEMO clinic seed (General / GP pilot)")
        delete_demo(db)
        seed = seed_demo(db)
        print(f"Demo clinic: Care Plus Clinic — DEMO (tenant {seed['tenant_id']})")
        print(f"Demo doctor: Dr. Sharma (DEMO) (doctor {seed['doctor_id']})")
        print(f"Demo owner mobile: +919000000000 (dummy) — one-time password printed above")
        clinic = db.query(Clinic).filter(Clinic.tenant_id == seed["tenant_id"]).first()

        step(2, "Simulated voice call (DialogueEngine, turn-by-turn)")
        engine = DialogueEngine(db, seed["tenant_id"], clinic)
        session: dict = {"call_id": "demo-call-001", "from_number": "+919000000001"}
        last: dict = {}
        for i, turn in enumerate(TURNS, 1):
            last = engine.process_text(session, turn) or {}
            print(f"Turn {i} — Patient: {turn}")
            print(f"         Assistant [{last.get('state')}]: {last.get('reply')}")
        appointment_id = last.get("appointment_id") or session.get("appointment_id")
        if not appointment_id:
            print("ERROR: call ke baad koi appointment nahi bani — state:", last.get("state"))
            return 1
        appt = db.query(Appointment).filter(Appointment.id == appointment_id).first()
        print(f"Appointment: {appt.id} — status {appt.status}, "
              f"slot {appt.slot_start}, booking amount Rs. {appt.booking_amount}")

        step(3, "Payment link (FR-26) — slot HELD_PENDING_PAYMENT, abhi CONFIRMED nahi")
        link = create_payment_link(db, appt)
        print(f"Payment link: {link['link']}")
        print(f"Amount: Rs. {link['amount']} | payment_id: {link['payment_id']} | status: {link['status']}")
        db.refresh(appt)
        assert appt.status == "HELD_PENDING_PAYMENT", appt.status

        step(4, "Simulated gateway webhook — payment SUCCESS")
        webhook = handle_payment_webhook(db, {
            "order_id": link["payment_id"],
            "gateway_payment_id": "cf_demo_0001",
            "status": "SUCCESS",
            "amount": link["amount"],
        })
        print(f"Webhook result: {webhook}")
        db.refresh(appt)
        print(f"Appointment status: {appt.status} | payment: {appt.payment_status} "
              f"| token: {appt.token_no}")
        assert appt.status == "CONFIRMED", appt.status

        step(5, "WhatsApp outbox (simulated)")
        print(f"Outbox messages: {len(wa.OUTBOX)}")
        for msg in wa.OUTBOX:
            print(f"  - [{msg['template_name']}] to {msg['to']}: {msg['body'][:160]}")
        rows = (db.query(Notification)
                .filter(Notification.tenant_id == seed["tenant_id"])
                .all())
        print(f"Notification rows in DB: {len(rows)}")

        step(6, "Daily digest (FR-13)")
        digest = build_daily_digest(db, seed["tenant_id"], appt.date)
        print(digest)

        print("\n==============================================")
        print("DEMO RESULT: appointment CONFIRMED")
        print(f"  Patient: Rahul Verma (DEMO) | Doctor: Dr. Sharma (DEMO)")
        print(f"  Date: {appt.date} {appt.slot_start.strftime('%H:%M')} | Token: {appt.token_no}")
        print(f"  Booking amount: Rs. {appt.booking_amount} paid, visit par adjust hoga")
        print("==============================================")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
