"""Clinic AI Assistant — FastAPI app (PRD v1.1).

Startup runs init_db() and seeds the DEMO clinic ONLY when DEMO_MODE is
true AND env SEED_DEMO=1 (default off in code — set it explicitly to
seed on boot). Simulated mode needs zero credentials.
"""
import os

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import SessionLocal, init_db
from .routers import admin, auth, booking, clinics, numbers_router, payments_router, voice, webhooks

APP_VERSION = "1.1.0"
# The dashboard no longer lives here: it is the static site in frontend/
# (same repo). / serves a JSON pointer to it; /health and /api/* are the API.

OPEN_DECISIONS = [
    {"id": "D1", "decision": "Pilot clinic kaunsi hai? Doctor ne haan bola hai?",
     "status": "OPEN", "default": "Bina D1 ke code likho, lekin go-live ka plan mat banao",
     "note": "Pilot specialty General / GP decide ho gayi hai; kaunsi clinic aur kaunse doctor — naam abhi open hai. Demo seed sirf DEMO clinic hai."},
    {"id": "D2", "decision": "Telephony vendor (Section 9.1 ke criteria par trial)",
     "status": "OPEN", "default": "1 hafte mein 2 vendors ka trial, phir lock"},
    {"id": "D3", "decision": "Clinic ka number: forwarding ya naya main number?",
     "status": "OPEN", "default": "Pilot mein forwarding"},
    {"id": "D4", "decision": "Fees call par batani hai? Kitni range mein?",
     "status": "OPEN", "default": "Sirf clinic ki likhi hui range; nahi to 'reception confirm karega'"},
    {"id": "D5", "decision": "Recording retention: audio kitne din, transcript kitne din?",
     "status": "OPEN", "default": "Audio 90 din, transcript 12 mahine — legal review ke baad final"},
    {"id": "D6", "decision": "Emergency script ka exact wording",
     "status": "OPEN", "default": "Doctor-approved bina live nahi"},
    {"id": "D7", "decision": "Pilot free hai ya nominal paid?",
     "status": "OPEN", "default": "Free, lekin likhit mein 7-din review aur success par paid convert ki baat saaf"},
    {"id": "D8", "decision": "Product / brand ka naam",
     "status": "DECIDED", "default": "Clinic AI Assistant (user decision, 18 Nov 2026)",
     "note": "Repo: clinic-ai-assistant. Generic descriptive words, no trademark claim."},
    {"id": "D9", "decision": "Booking amount: ON/OFF, Rs. 50 ya Rs. 100, timely-cancel cut-off, late-cancel policy (FR-26)",
     "status": "OPEN", "default": "ON, Rs. 100, 4 ghante, late-cancel forfeit — aakhri shabd clinic ka",
     "note": "PRD Sec 6 (FR-26) se; final call pilot clinic ke saath."},
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.demo_mode and os.getenv("SEED_DEMO", "0") == "1":
        from .seed import seed_demo

        db = SessionLocal()
        try:
            seed_demo(db)
        finally:
            db.close()
    # Platform super-admin bootstrap (env PLATFORM_ADMIN_MOBILE +
    # PLATFORM_ADMIN_PASSWORD; no-op when unset — see seed.py).
    try:
        from .seed import ensure_platform_admin

        db = SessionLocal()
        try:
            ensure_platform_admin(db)
        finally:
            db.close()
    except Exception:
        pass
    yield


app = FastAPI(title="Clinic AI Assistant", version=APP_VERSION, lifespan=lifespan)

# CORS — the dashboard is a separate static site (Vercel), so the browser
# calls this API cross-origin. FRONTEND_ORIGIN (comma-separated) is the
# allow-list; in DEMO_MODE with no value set, '*' keeps local dev working;
# in prod with no value set, no cross-origin calls are allowed.
if settings.frontend_origin:
    _allow_origins = [o.strip() for o in settings.frontend_origin.split(",") if o.strip()]
elif settings.demo_mode:
    _allow_origins = ["*"]
else:
    _allow_origins = []
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(clinics.router)
app.include_router(booking.router)
app.include_router(voice.router)
app.include_router(payments_router.router)
app.include_router(numbers_router.router)
app.include_router(webhooks.router)
app.include_router(admin.router)


@app.get("/health")
def health():
    mode = "simulated" if settings.demo_mode else "live"
    return {"status": "ok", "app": "Clinic AI Assistant",
            "version": APP_VERSION, "mode": mode}


@app.get("/api/open-decisions")
def open_decisions():
    return {"decisions": OPEN_DECISIONS}


@app.get("/")
def root():
    return {"app": "Clinic AI Assistant", "version": APP_VERSION,
            "message": "Backend API — dashboard is the static site in frontend/ (Vercel). See /health and /docs."}
