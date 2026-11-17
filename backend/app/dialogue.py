"""Dialogue engine — PRD Sec 7 (call flow) and Sec 11 (deterministic rules).

Rule-based extraction first; safety (emergency / medical-advice) is checked
BEFORE any intent or booking step. The LLM is never required and never
decides a booking — slots and bookings come from slot_engine + an explicit
confirm-back in a later turn. FAQ answers come ONLY from clinic config
fields, never from the model's memory.
"""
import json
import os
import re
from datetime import datetime

from sqlalchemy.orm import Session

from .date_parser import (
    format_confirm_date,
    now_ist,
    parse_date_hint,
    parse_datetime_hint,
    parse_time_hint,
)
from .models import Appointment, CallbackTicket, Clinic, Doctor, Patient
from .safety import (
    deflection_response,
    detect_advice_request,
    detect_emergency,
    emergency_script,
)
from .schemas import BookingExtraction
from .slot_engine import (
    book_slot,
    find_alternatives,
    generate_slots,
    hold_slot,
    is_slot_free,
)

# States (CONTRACT.md). PAYMENT_PENDING is added by PRD v1.1 FR-26.
STATE_GREET = "GREET"
STATE_CONSENT = "CONSENT"
STATE_INTENT = "INTENT"
STATE_COLLECT = "COLLECT"
STATE_CHECK_SLOTS = "CHECK_SLOTS"
STATE_CONFIRM_BACK = "CONFIRM_BACK"
STATE_BOOK = "BOOK"
STATE_NOTIFY = "NOTIFY"
STATE_CLOSE = "CLOSE"
STATE_HUMAN = "HUMAN"
STATE_EMERGENCY = "EMERGENCY"
STATE_PAYMENT_PENDING = "PAYMENT_PENDING"

_YES = {"haan", "ha", "yes", "yeah", "ok", "okay", "theek hai", "thik hai",
        "ji haan", "bilkul", "sure", "confirm", "confirm karo", "haan ji",
        "kar do", "sahi hai", "yes please"}
_NO = {"nahi", "nahin", "naheen", "no", "nope", "galat hai", "galat",
       "mat karo", "cancel karo", "nahi chahiye"}

_HUMAN_WORDS = ["insaan", "reception", "agent", "human", "operator",
                "kisi se baat", "aadmi se", "customer care", "staff se"]
_SYMPTOMS = ["bukhar", "fever", "sardi", "cold", "khansi", "cough",
             "pet dard", "stomach", "sir dard", "headache", "dard", "pain",
             "chakkar", "dizziness", "allergy", "skin", "daant", "tooth",
             "gale", "throat", "kamzori", "weakness", "ulti", "vomit",
             "diabetes", "sugar", "bp", "asthma", "saans"]
_NAME_STOP = {"hai", "hain", "bol", "raha", "rahi", "hoon", "hu", "ji", "ko",
              "se", "ke", "saath", "mera", "mobile", "aur", "kal", "aaj"}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
             "saturday", "sunday"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december"]


# --------------------------------------------------------------------------
# Rule-based extraction (PRD Appendix B)
# --------------------------------------------------------------------------
def _clean_name(raw: str) -> str | None:
    words = [w for w in raw.strip().split() if w.lower().strip(",") not in _NAME_STOP]
    words = [w.strip(",") for w in words][:2]
    if not words or any(not w.isalpha() for w in words):
        return None
    return " ".join(w.capitalize() for w in words)


def _extract_mobile(text: str) -> str | None:
    m = re.search(r"(?:\+?91[\s\-]?)?([6-9]\d{9})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([6-9]\d{4})[\s\-](\d{5})\b", text)
    if m:
        return m.group(1) + m.group(2)
    return None


def detect_language(text: str) -> str:
    """FR-02 (lightweight, rule-based): first utterance se language.

    Devanagari script -> "hindi" (pure Devanagari) ya "hinglish" (mix);
    sirf English words -> "english"; default "hinglish" — Bangalore ki
    aam boli. Ye sirf greeting / language_pref ke liye hai; koi booking
    decision is par nahi hoti. Full Kannada support P1 milestone hai.
    """
    t = (text or "").strip()
    if not t:
        return "hinglish"
    deva = sum(1 for ch in t if "\u0900" <= ch <= "\u097f")
    letters = [ch for ch in t if ch.isalpha()]
    if deva and deva >= max(1, len(letters) // 2):
        return "hindi"
    if deva:
        return "hinglish"
    if re.search(r"\b(mujhe|mera|meri|kya|hai|hain|kal|aaj|parso|shaam|subah|baje|dikhana|chahiye|karna|kitne)\b", t.lower()):
        return "hinglish"
    return "english"


def _extract_date_hint(low: str) -> str | None:
    m = re.search(r"\d{1,2}\s+(?:" + "|".join(_MONTHS) + r")\b", low)
    if m:
        return m.group(0)
    m = re.search(r"\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?", low)
    if m:
        return m.group(0)
    for wd in _WEEKDAYS:
        if re.search(rf"\b{wd}\b", low):
            return wd
    for word in ("parso", "day after tomorrow", "kal", "tomorrow", "aaj", "today"):
        if re.search(rf"\b{word}\b", low):
            return word
    return None


_PERIODS = ("shaam", "sham", "subah", "dopahar", "raat", "morning", "evening", "afternoon")


def _extract_period(low: str) -> str | None:
    """Period word on its own ("kal shaam", "shaam ko") — the caller often
    says the period in one turn and the hour in a later turn ("5 baje").
    Remember it so a bare hour resolves to the right half of the day."""
    for w in _PERIODS:
        if re.search(rf"\b{w}\b", low):
            return w
    return None

def _extract_time_hint(low: str) -> str | None:
    m = re.search(
        r"(?:subah|shaam|sham|dopahar|raat|morning|evening|afternoon)"
        r"\s+\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?", low)
    if m:
        return m.group(0).strip()
    m = re.search(r"\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)\b", low)
    if m:
        return m.group(0).strip()
    return None


def _extract_reason(low: str) -> str | None:
    for kw in _SYMPTOMS:
        if kw in low:
            words = low[low.index(kw):].split()[:8]
            words = [w for w in words
                     if w not in {"hai", "hain", "kal", "parso", "aaj", "subah",
                                  "shaam", "baje", "aur", "mujhe", "mere", "ko"}]
            words = [w for w in words if not re.fullmatch(r"\d+", w)][:8]
            return " ".join(words) if words else kw
    m = re.search(r"(?:mujhe|mere ko|i have|problem|takleef)\s+([^,.;]+)", low)
    if m:
        words = m.group(1).split()[:8]
        return " ".join(words) if words else None
    return None


def _llm_supplement(text: str, ext: BookingExtraction) -> BookingExtraction:
    """Best-effort LLM fill-in for MISSING fields only.

    Only runs when GROQ_API_KEY is set; never required, never overrides a
    rule-based value, and never used for any booking decision (PRD Sec 11).
    Any failure silently returns the rule-based extraction unchanged.
    """
    if not os.getenv("GROQ_API_KEY"):
        return ext
    if ext.patient_name and ext.mobile and ext.date_hint and ext.time_hint:
        return ext
    try:
        import httpx

        from .config import settings

        prompt = (
            "Extract only these fields from the patient message as JSON: "
            "patient_name, mobile (10 digits), date_hint, time_hint, reason_line "
            "(max 8 words). Use null for anything not stated. Message: " + text
        )
        resp = httpx.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
            json={"model": settings.groq_llm_model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0, "max_tokens": 200},
            timeout=10,
        )
        content = resp.json()["choices"][0]["message"]["content"]
        data = json.loads(content[content.index("{"):content.rindex("}") + 1])
        if not ext.patient_name and data.get("patient_name"):
            ext.patient_name = _clean_name(str(data["patient_name"]))
        if not ext.mobile and data.get("mobile"):
            mob = _extract_mobile(str(data["mobile"]))
            if mob:
                ext.mobile = mob
        if not ext.date_hint and data.get("date_hint"):
            ext.date_hint = str(data["date_hint"])
        if not ext.time_hint and data.get("time_hint"):
            ext.time_hint = str(data["time_hint"])
        if not ext.reason_line and data.get("reason_line"):
            ext.reason_line = " ".join(str(data["reason_line"]).split()[:8])
    except Exception:
        return ext
    return ext


def extract_fields(text: str, clinic_name: str = "") -> BookingExtraction:
    """Rule-based extraction of a booking turn (PRD Appendix B).

    Safety intents come first: emergency -> ``emergency``; a pure medical-
    advice question has no bookable intent in the schema, so it returns
    ``unknown`` here — DialogueEngine deflects it via safety.py before
    this result is ever used for booking.
    """
    low = (text or "").lower()
    if detect_emergency(text or ""):
        return BookingExtraction(intent="emergency", confidence=0.95)

    name = None
    m = re.search(r"(?:mera naam|my name is|naam)\s+(?:hai\s+)?([A-Za-z]+(?:\s+[A-Za-z]+)?)", text or "")
    if m:
        name = _clean_name(m.group(1))
    if not name:
        m = re.search(r"\bmain\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+bol", text or "")
        if m:
            name = _clean_name(m.group(1))
    mobile = _extract_mobile(text or "")

    doctor_hint = None
    m = re.search(r"\bDr\.?\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)", text or "", re.IGNORECASE)
    if m:
        doctor_hint = _clean_name(m.group(1))
    if not doctor_hint:
        m = re.search(r"\bdoctor\s+([A-Za-z]+(?:\s+[A-Za-z]+)?)", text or "", re.IGNORECASE)
        if m:
            doctor_hint = _clean_name(m.group(1))

    date_hint = _extract_date_hint(low)
    time_hint = _extract_time_hint(low)
    reason = _extract_reason(low)

    if any(w in low for w in _HUMAN_WORDS):
        intent = "human"
    elif re.search(r"reschedule|dobara|shift|badal|change|dusre din|aage\s+badha", low):
        intent = "reschedule"
    elif re.search(r"\bcancel\b|radd|nahi aana|appointment hat", low):
        intent = "cancel"
    elif re.search(r"fees|charges|kitna|kharcha|timing|kab khulta|kab band|band hota|kab tak|"
                   r"khula|address|kahan hai|location|pata|opening|closing|kitne baje khulta|"
                   r"kab milenge|doctor kab|kaunsa clinic|kaun sa clinic", low):
        intent = "faq"
    elif re.search(r"appointment|booking|book|dikhana|milna|checkup|check-up|doctor|"
                   r"milna hai|appointment chahiye", low) or date_hint or time_hint:
        intent = "book"
    elif (name or mobile) and not detect_advice_request(text or ""):
        # Name/mobile with no other cue: a booking turn in progress.
        intent = "book"
    else:
        intent = "unknown"

    if intent == "unknown":
        # Pure advice question / noise: no bookable intent (see docstring).
        # A bare name/mobile/date answer still counts as partial info.
        confidence = 0.75 if (name or mobile or date_hint or time_hint) else 0.2
    elif name and time_hint:
        confidence = 0.9
    elif name or mobile or (date_hint and time_hint):
        confidence = 0.8
    elif date_hint or time_hint or doctor_hint or reason:
        confidence = 0.75
    else:
        confidence = 0.8  # clear intent keyword, no fields yet

    ext = BookingExtraction(
        intent=intent, patient_name=name, mobile=mobile,
        doctor_hint=doctor_hint, date_hint=date_hint, time_hint=time_hint,
        reason_line=reason, confidence=confidence,
    )
    return _llm_supplement(text or "", ext)


# --------------------------------------------------------------------------
# Dialogue engine
# --------------------------------------------------------------------------
class DialogueEngine:
    """State machine for one voice call (PRD Sec 7).

    ``session`` is a plain dict the caller keeps between turns (e.g. in the
    Call row / KV store); it is mutated in place and returned via ``state``.
    Every DB query filters by ``tenant_id``.
    """

    def __init__(self, db: Session, tenant_id: str, clinic):
        self.db = db
        self.tenant_id = tenant_id
        self.clinic = clinic

    # -- clinic config helpers (Clinic ORM object or plain dict) -----------
    def _cfg(self, name: str, default=""):
        c = self.clinic
        if c is None:
            return default
        if isinstance(c, dict):
            return c.get(name, default) or default
        return getattr(c, name, default) or default

    @property
    def _clinic_name(self) -> str:
        return self._cfg("display_name", "Clinic AI Assistant")

    @property
    def _booking_amount_enabled(self) -> bool:
        c = self.clinic
        if isinstance(c, dict):
            return bool(c.get("booking_amount_enabled", False))
        return bool(getattr(c, "booking_amount_enabled", False)) if c else False

    # -- small helpers ------------------------------------------------------
    @staticmethod
    def _fmt_time(dt: datetime) -> str:
        h = dt.hour % 12 or 12
        period = "subah" if dt.hour < 12 else ("shaam" if dt.hour < 18 else "raat")
        return f"{period} {h}:{dt.minute:02d} baje"

    def _greeting(self) -> str:
        custom = self._cfg("greeting_text", "")
        base = custom or f"Namaste! Main {self._clinic_name} ka digital assistant hoon."
        return (
            f"{base} Ye call record ho rahi hai — kya aap iski consent dete hain? "
            "Haan bolkar aage badhein."
        )

    def _create_callback_ticket(self, session: dict, reason: str = "") -> None:
        try:
            ticket = CallbackTicket(
                tenant_id=self.tenant_id,
                name=session.get("patient_name") or "",
                mobile=session.get("mobile") or "",
                reason=(reason or session.get("reason_line") or "")[:200],
                priority="high" if session.get("emergency_alert") else "normal",
                status="open",
            )
            self.db.add(ticket)
            self.db.commit()
        except Exception:
            self.db.rollback()

    def _handoff(self, session: dict, reason: str = "") -> dict:
        self._create_callback_ticket(session, reason or "human handoff requested")
        session["state"] = STATE_HUMAN
        return {
            "reply": "Theek hai, main aapko reception se connect kar raha hoon — "
                     "reception aapko wapas call karegi. Dhanyavaad.",
            "state": STATE_HUMAN,
            "action": "human_handoff",
        }

    def _resolve_doctor(self, session: dict) -> Doctor | None:
        hint = (session.get("doctor_hint") or "").lower()
        doctors = (
            self.db.query(Doctor)
            .filter(Doctor.tenant_id == self.tenant_id, Doctor.active.is_(True))
            .order_by(Doctor.name).all()
        )
        if not doctors:
            return None
        if hint:
            for d in doctors:
                if hint in d.name.lower() or d.name.lower() in hint:
                    return d
        return doctors[0]

    def _resolve_slot_datetime(self, session: dict) -> datetime | None:
        date_hint = session.get("date_hint") or ""
        time_hint = session.get("time_hint") or ""
        period = session.get("time_period") or ""
        if time_hint and period and not any(w in time_hint.lower() for w in _PERIODS):
            time_hint = f"{period} {time_hint}"
        combined = " ".join(x for x in (date_hint, time_hint) if x)
        dt = parse_datetime_hint(combined) if combined else None
        if dt:
            return dt
        d = parse_date_hint(date_hint or combined)
        t = parse_time_hint(time_hint or combined)
        if d and t:
            return datetime.combine(d, t)
        return None

    def _get_or_create_patient(self, session: dict) -> Patient | None:
        mobile = session.get("mobile")
        if not mobile:
            return None
        patient = (
            self.db.query(Patient)
            .filter(Patient.tenant_id == self.tenant_id, Patient.mobile == mobile)
            .first()
        )
        if patient:
            if session.get("patient_name") and patient.name != session["patient_name"]:
                patient.name = session["patient_name"]
                self.db.commit()
            return patient
        patient = Patient(
            tenant_id=self.tenant_id,
            name=session.get("patient_name") or "Patient",
            mobile=mobile, language_pref="hinglish",
        )
        self.db.add(patient)
        self.db.commit()
        self.db.refresh(patient)
        return patient

    # -- main entry point ---------------------------------------------------
    def process_text(self, session: dict, text: str) -> dict:
        text = (text or "").strip()
        low = text.lower()
        state = session.get("state") or STATE_GREET
        # FR-02: pehle utterance se language detect karo, beech call mein
        # switch ho to follow karo (session par store; booking par asar nahi).
        if text and not session.get("language_locked"):
            session["language"] = detect_language(text)

        # 1) Safety FIRST — before any intent or booking step (PRD Sec 7).
        if detect_emergency(text):
            session["state"] = STATE_EMERGENCY
            session["emergency_alert"] = True
            return {"reply": emergency_script(), "state": STATE_EMERGENCY,
                    "action": "emergency"}
        if detect_advice_request(text):
            if state in (STATE_GREET, STATE_CONSENT):
                session["state"] = STATE_INTENT
                state = STATE_INTENT
            return {"reply": deflection_response() + " Kya main aapki appointment book kar doon?",
                    "state": session.get("state", STATE_INTENT),
                    "action": "deflect_advice"}
        if any(w in low for w in _HUMAN_WORDS):
            return self._handoff(session, "caller asked for a human")

        # Period memory: "kal shaam" in turn 1 + "5 baje" in turn 4 must
        # resolve to 17:00, not 05:00. Stored on every turn, latest wins.
        _period = _extract_period(low)
        if _period:
            session["time_period"] = _period

        # 2) Fresh session -> greeting + recording-consent line (PRD Sec 7).
        # The caller's FIRST utterance usually already says the whole job
        # ("mujhe kal shaam Dr. Sharma ko dikhana hai") — extract it now
        # and keep it, so the consent step does not throw it away. Without
        # this the engine re-asks intent from scratch after consent and a
        # bare name ("Rahul Verma") looks like a failed intent.
        if state == STATE_GREET:
            session["state"] = STATE_CONSENT
            session["consent_line_played"] = True
            _first = extract_fields(text)
            self._merge(session, _first)
            session["pending_intent"] = _first.intent
            session["pending_text"] = text
            return {"reply": self._greeting(), "state": STATE_CONSENT,
                    "action": "greet"}

        if state == STATE_CONSENT:
            if low in _YES or any(w in low for w in ("haan", "yes", "consent", "theek")):
                # The consent answer itself may carry fields ("Haan, Rahul
                # Verma") — merge them, then resume the job from the FIRST
                # utterance instead of asking intent all over again.
                self._merge(session, extract_fields(text))
                pending = session.get("pending_intent")
                if pending == "book":
                    session["pending_action"] = "book"
                    session["state"] = STATE_COLLECT
                    return self._collect_next(session)
                if pending in ("reschedule", "cancel"):
                    ext = BookingExtraction(
                        intent=pending,
                        patient_name=session.get("patient_name"),
                        mobile=session.get("mobile"),
                        doctor_hint=session.get("doctor_hint"),
                        date_hint=session.get("date_hint"),
                        time_hint=session.get("time_hint"),
                        reason_line=session.get("reason_line"),
                        confidence=0.9)
                    return self._start_reschedule_cancel(
                        session, ext, session.get("pending_text") or "")
                if pending == "faq":
                    return self._answer_faq(session, session.get("pending_text") or text)
                session["state"] = STATE_INTENT
                return {"reply": "Dhanyavaad. Aapko appointment book karni hai, "
                                 "ya clinic ke baare mein kuch poochna hai?",
                        "state": STATE_INTENT, "action": "consent_given"}
            if low in _NO or "nahi" in low or "no" in low:
                return self._handoff(session, "recording consent declined")
            session["failed_attempts"] = session.get("failed_attempts", 0) + 1
            if session["failed_attempts"] >= 2:
                return self._handoff(session, "consent not understood twice")
            return {"reply": "Maaf kijiye, samajh nahi aaya. Ye call record hogi — "
                             "kya aap consent dete hain? Haan ya nahi boliye.",
                    "state": STATE_CONSENT, "action": "clarify"}

        if state == STATE_EMERGENCY:
            return {"reply": emergency_script(), "state": STATE_EMERGENCY,
                    "action": "emergency"}
        if state == STATE_HUMAN:
            return {"reply": "Reception aapko jald wapas call karegi. Dhanyavaad.",
                    "state": STATE_HUMAN, "action": "human_handoff"}
        if state == STATE_CLOSE:
            return {"reply": "Dhanyavaad, phir milenge. Namaste!",
                    "state": STATE_CLOSE, "action": "close"}
        if state == STATE_PAYMENT_PENDING:
            return {"reply": "Aapki appointment payment pending hai — payment link "
                             "par payment karte hi booking confirm ho jayegi. "
                             "Madad ke liye reception se baat kar sakte hain.",
                    "state": STATE_PAYMENT_PENDING, "action": "payment_pending"}
        if state == STATE_BOOK:
            session["state"] = STATE_NOTIFY
            self._notify(session)
            return {"reply": "Aapko WhatsApp par booking confirmation bhej di gayi hai. "
                             "Dhanyavaad!",
                    "state": STATE_NOTIFY, "action": "notify"}
        if state == STATE_NOTIFY:
            session["state"] = STATE_CLOSE
            return {"reply": "Dhanyavaad, phir milenge. Namaste!",
                    "state": STATE_CLOSE, "action": "close"}

        if state == STATE_INTENT:
            return self._handle_intent(session, text)
        if state == STATE_COLLECT:
            return self._handle_collect(session, text)
        if state == STATE_CHECK_SLOTS:
            return self._handle_check_slots(session, text)
        if state == STATE_CONFIRM_BACK:
            return self._handle_confirm_back(session, text)

        session["state"] = STATE_INTENT
        return {"reply": "Aapko appointment book karni hai, ya kuch aur poochna hai?",
                "state": STATE_INTENT, "action": "clarify"}

    # -- intent routing ------------------------------------------------------
    def _handle_intent(self, session: dict, text: str) -> dict:
        ext = extract_fields(text)
        if ext.intent == "emergency":
            session["state"] = STATE_EMERGENCY
            session["emergency_alert"] = True
            return {"reply": emergency_script(), "state": STATE_EMERGENCY,
                    "action": "emergency"}
        if ext.intent == "human":
            return self._handoff(session, "caller asked for a human")
        if ext.intent == "faq":
            return self._answer_faq(session, text)
        if ext.intent in ("reschedule", "cancel"):
            return self._start_reschedule_cancel(session, ext, text)
        # Reschedule/cancel is waiting on the mobile number: now that we
        # have it, look the old booking up before asking anything else.
        if session.get("pending_action") in ("reschedule", "cancel") \
                and not session.get("pending_appointment_id") \
                and session.get("mobile"):
            return self._start_reschedule_cancel(session, ext, text)
        if ext.intent == "book":
            self._merge(session, ext)
            session["pending_action"] = "book"
            session["state"] = STATE_COLLECT
            return self._collect_next(session)
        # unknown / sparse -> one clarifying question, then human (PRD Sec 7).
        session["failed_attempts"] = session.get("failed_attempts", 0) + 1
        if session["failed_attempts"] >= 2:
            return self._handoff(session, "could not understand after 2 attempts")
        return {"reply": "Maaf kijiye, samajh nahi aaya. Aapko appointment book karni hai, "
                         "ya clinic ke baare mein kuch poochna hai? Ek baar phir boliye.",
                "state": STATE_INTENT, "action": "clarify"}

    def _merge(self, session: dict, ext: BookingExtraction) -> None:
        if ext.patient_name:
            session["patient_name"] = ext.patient_name
        if ext.mobile:
            session["mobile"] = ext.mobile
        if ext.doctor_hint:
            session["doctor_hint"] = ext.doctor_hint
        if ext.date_hint:
            session["date_hint"] = ext.date_hint
        if ext.time_hint:
            session["time_hint"] = ext.time_hint
        if ext.reason_line:
            session["reason_line"] = ext.reason_line

    def _handle_collect(self, session: dict, text: str) -> dict:
        ext = extract_fields(text)
        self._merge(session, ext)
        # Caller answered a name question with just their name.
        if not session.get("patient_name") and ext.intent == "unknown":
            words = [w for w in text.split() if w.isalpha() and w.lower() not in _YES | _NO]
            if 1 <= len(words) <= 2 and len(text) <= 30:
                session["patient_name"] = " ".join(w.capitalize() for w in words)
        # Caller answered a mobile question with just digits.
        if not session.get("mobile") and ext.intent == "unknown":
            mob = _extract_mobile(text)
            if mob:
                session["mobile"] = mob
        if ext.intent == "faq":
            return self._answer_faq(session, text)
        if ext.intent in ("reschedule", "cancel"):
            return self._start_reschedule_cancel(session, ext, text)
        # Reschedule/cancel is waiting on the mobile number: now that we
        # have it, look the old booking up before asking anything else.
        if session.get("pending_action") in ("reschedule", "cancel") \
                and not session.get("pending_appointment_id") \
                and session.get("mobile"):
            return self._start_reschedule_cancel(session, ext, text)
        if ext.intent == "unknown" and not any(
                session.get(k) for k in ("patient_name", "mobile", "date_hint", "time_hint")):
            session["failed_attempts"] = session.get("failed_attempts", 0) + 1
            if session["failed_attempts"] >= 2:
                return self._handoff(session, "could not understand after 2 attempts")
            return {"reply": "Maaf kijiye, samajh nahi aaya. Ek baar phir boliye.",
                    "state": STATE_COLLECT, "action": "clarify"}
        session["failed_attempts"] = 0
        return self._collect_next(session)

    def _collect_next(self, session: dict) -> dict:
        """One question at a time (PRD Sec 7); slots only when all fields exist."""
        # FR-16 returning patient: mobile pata hai aur naam abhi nahi
        # poochha gaya — purana patient ho to naam auto-fill, dobara mat
        # poochho; greeting personalised ho jati hai (session flag).
        if not session.get("patient_name") and session.get("mobile"):
            try:
                known = (self.db.query(Patient)
                         .filter(Patient.tenant_id == self.tenant_id,
                                 Patient.mobile == session["mobile"]).first())
            except Exception:
                known = None
            if known and known.name and known.name != "Patient":
                session["patient_name"] = known.name
                session["returning_patient"] = True
                session["language"] = known.language_pref or session.get("language") or "hinglish"
        if not session.get("patient_name"):
            session["state"] = STATE_COLLECT
            return {"reply": "Aapka naam kya hai?",
                    "state": STATE_COLLECT, "action": "ask_name"}
        if not session.get("mobile"):
            session["state"] = STATE_COLLECT
            return {"reply": "Apna 10-digit mobile number batayein, please.",
                    "state": STATE_COLLECT, "action": "ask_mobile"}
        if not session.get("date_hint") and not session.get("time_hint"):
            session["state"] = STATE_COLLECT
            return {"reply": "Kis din aur kitne baje aana hai? Jaise — kal subah 10 baje.",
                    "state": STATE_COLLECT, "action": "ask_datetime"}
        if session.get("date_hint") and not session.get("time_hint"):
            session["state"] = STATE_COLLECT
            return {"reply": "Kis samay aana hai? Subah ya shaam, kitne baje?",
                    "state": STATE_COLLECT, "action": "ask_time"}
        if session.get("time_hint") and not session.get("date_hint"):
            session["state"] = STATE_COLLECT
            return {"reply": "Kis din aana hai — aaj, kal ya parso?",
                    "state": STATE_COLLECT, "action": "ask_date"}
        return self._check_slots(session)

    # -- slots ---------------------------------------------------------------
    def _check_slots(self, session: dict) -> dict:
        session["state"] = STATE_CHECK_SLOTS
        wanted = self._resolve_slot_datetime(session)
        if not wanted:
            session["state"] = STATE_COLLECT
            return {"reply": "Din aur samay samajh nahi aaya. Jaise boliye — kal subah 10 baje.",
                    "state": STATE_COLLECT, "action": "clarify"}
        doctor = self._resolve_doctor(session)
        if not doctor:
            return {"reply": "Is samay doctor ki availability reception se confirm "
                             "karke batayenge.",
                    "state": STATE_INTENT, "action": "no_doctor"}
        session["doctor_id"] = doctor.id
        session["doctor_name"] = doctor.name
        slots = generate_slots(self.db, doctor.id, wanted.date(), doctor.slot_minutes)
        if wanted in slots and is_slot_free(self.db, doctor.id, wanted):
            session["slot_start"] = wanted.isoformat()
            session["state"] = STATE_CONFIRM_BACK
            return {"reply": self._confirm_text(session, wanted, doctor),
                    "state": STATE_CONFIRM_BACK, "action": "confirm_back"}
        # Slot full -> exactly 2 alternatives (PRD FR-06).
        alts = find_alternatives(self.db, doctor.id, wanted, doctor.slot_minutes, n=2)
        session["alternatives"] = [a.isoformat() for a in alts]
        if not alts:
            return {"reply": "Is hafte koi slot khaali nahi hai. Reception se confirm "
                             "karke batayenge.",
                    "state": STATE_INTENT, "action": "slot_full"}
        lines = ", ".join(
            f"{i + 1}. {format_confirm_date(a)}, {self._fmt_time(a)}"
            for i, a in enumerate(alts))
        return {"reply": f"Wo samay khaali nahi hai. Aap inme se chun sakte hain: {lines}. "
                         "Pehla ya dusra boliye.",
                "state": STATE_CHECK_SLOTS, "action": "slot_full"}

    def _handle_check_slots(self, session: dict, text: str) -> dict:
        low = text.lower()
        alts = [datetime.fromisoformat(x) for x in session.get("alternatives") or []]
        chosen = None
        if alts:
            if low in _YES or "sahi hai" in low or "theek hai" in low:
                chosen = alts[0]  # "haan" after an offer = yes to the first (nearest)
            elif any(w in low for w in ("pehla", "pehli", "first", "1")):
                chosen = alts[0]
            elif any(w in low for w in ("dusra", "dusri", "second", "2")) and len(alts) > 1:
                chosen = alts[1]
            else:
                t = parse_time_hint(text)
                for a in alts:
                    if t and a.time() == t:
                        chosen = a
                        break
        if chosen:
            session["slot_start"] = chosen.isoformat()
            session["date_hint"] = format_confirm_date(chosen)
            session["time_hint"] = self._fmt_time(chosen)
            doctor = self._resolve_doctor(session)
            session["state"] = STATE_CONFIRM_BACK
            return {"reply": self._confirm_text(session, chosen, doctor),
                    "state": STATE_CONFIRM_BACK, "action": "confirm_back"}
        # New date/time offered instead of an alternative.
        ext = extract_fields(text)
        self._merge(session, ext)
        return self._check_slots(session)

    def _confirm_text(self, session: dict, slot: datetime, doctor) -> str:
        mobile = session.get("mobile") or ""
        return (
            "Confirm karein — Naam: {name}, Mobile: ****{last4}, "
            "Din: {date}, Samay: {time}, Doctor: {doctor}. "
            "Kya ye sahi hai? Haan bolkar confirm karein."
        ).format(
            name=session.get("patient_name") or "",
            last4=mobile[-4:] if len(mobile) >= 4 else mobile,
            date=format_confirm_date(slot),
            time=self._fmt_time(slot),
            doctor=doctor.name if doctor else "",
        )

    # -- confirm-back / booking ----------------------------------------------
    def _handle_confirm_back(self, session: dict, text: str) -> dict:
        low = text.lower()
        if low in _YES or any(w in low for w in ("haan", "yes", "confirm", "sahi hai", "kar do")):
            return self._do_book_or_action(session)
        if low in _NO or any(w in low for w in ("nahi", "galat", "badlo", "change")):
            session["state"] = STATE_COLLECT
            session.pop("slot_start", None)
            return {"reply": "Theek hai, kya badalna hai — naam, din ya samay? Batayein.",
                    "state": STATE_COLLECT, "action": "clarify"}
        ext = extract_fields(text)
        self._merge(session, ext)
        if session.get("date_hint") or session.get("time_hint"):
            return self._check_slots(session)
        return {"reply": "Kya ye details sahi hain? Haan bolkar confirm karein, "
                         "ya nahi bolkar badal dein.",
                "state": STATE_CONFIRM_BACK, "action": "confirm_back"}

    def _do_book_or_action(self, session: dict) -> dict:
        action = session.get("pending_action") or "book"
        if action == "cancel":
            appt_id = session.get("pending_appointment_id")
            appt = self.db.query(Appointment).filter(
                Appointment.tenant_id == self.tenant_id,
                Appointment.id == appt_id).first() if appt_id else None
            if not appt:
                session["state"] = STATE_INTENT
                return {"reply": "Appointment nahi mili. Reception se confirm karke batayenge.",
                        "state": STATE_INTENT, "action": "lookup_failed"}
            appt.status = "CANCELLED"
            self.db.commit()
            session["state"] = STATE_CLOSE
            return {"reply": f"Aapki appointment {format_confirm_date(appt.slot_start)}, "
                             f"{self._fmt_time(appt.slot_start)} wali cancel kar di gayi hai. "
                             "Dhanyavaad.",
                    "state": STATE_CLOSE, "action": "cancelled",
                    "appointment_id": appt.id}

        patient = self._get_or_create_patient(session)
        slot = datetime.fromisoformat(session["slot_start"]) if session.get("slot_start") else None
        doctor_id = session.get("doctor_id")
        if not patient or not slot or not doctor_id:
            session["state"] = STATE_COLLECT
            return {"reply": "Kuch details adhoori hain. Apna naam, mobile aur samay phir boliye.",
                    "state": STATE_COLLECT, "action": "clarify"}
        duration = 15
        doctor = self.db.query(Doctor).filter(Doctor.id == doctor_id).first()
        if doctor:
            duration = doctor.slot_minutes
        reason = session.get("reason_line") or ""

        if self._booking_amount_enabled:
            amount = self._booking_amount(session)
            hold_seconds = int(self._cfg("hold_minutes", 15) or 15) * 60
            appt = hold_slot(self.db, self.tenant_id, doctor_id, patient.id,
                             slot, duration, reason_line=reason,
                             hold_seconds=hold_seconds)
            if not appt:
                session["state"] = STATE_CHECK_SLOTS
                return self._check_slots(session)
            appt.booking_amount = amount
            appt.payment_status = "pending"
            self.db.commit()
            session["state"] = STATE_PAYMENT_PENDING
            session["appointment_id"] = appt.id
            if action == "reschedule":
                self._mark_old_rescheduled(session)
            # FR-26: amount + 3 rules in one line; booking is NOT confirmed yet.
            return {
                "reply": f"Appointment hold ho gayi hai. Booking amount ₹{amount} hai — "
                         "visit par ye amount adjust ho jayega / samay par cancel karne par "
                         "poora refund milega / bina bataye na aane par amount forfeit ho jayega. "
                         "Payment link par payment karte hi booking confirm ho jayegi.",
                "state": STATE_PAYMENT_PENDING, "action": "payment_pending",
                "appointment_id": appt.id,
            }

        appt = book_slot(self.db, self.tenant_id, doctor_id, patient.id,
                         slot, duration, reason_line=reason,
                         booking_amount=0, payment_status="not_required")
        if not appt:
            session["state"] = STATE_CHECK_SLOTS
            return self._check_slots(session)
        if action == "reschedule":
            self._mark_old_rescheduled(session)
        session["state"] = STATE_BOOK
        session["appointment_id"] = appt.id
        return {
            "reply": f"Ho gaya! Aapki appointment confirm hai — "
                     f"{format_confirm_date(appt.slot_start)}, {self._fmt_time(appt.slot_start)}, "
                     f"Doctor {self._doctor_name(doctor_id)}, "
                     f"Token No. {appt.token_no}. Dhanyavaad!",
            "state": STATE_BOOK, "action": "booked",
            "appointment_id": appt.id,
        }

    def _doctor_name(self, doctor_id: str) -> str:
        d = self.db.query(Doctor).filter(Doctor.id == doctor_id).first()
        return d.name if d else ""

    def _mark_old_rescheduled(self, session: dict) -> None:
        old_id = session.get("old_appointment_id")
        if not old_id:
            return
        old = self.db.query(Appointment).filter(
            Appointment.tenant_id == self.tenant_id,
            Appointment.id == old_id).first()
        if old:
            old.status = "RESCHEDULED"
            self.db.commit()

    def _booking_amount(self, session: dict | None = None) -> int:
        """FR-26 amount: payments module when present, else clinic default."""
        try:
            from .payments import booking_amount_for  # lazy: avoids circular import

            return int(booking_amount_for(self.clinic))
        except Exception:
            return int(self._cfg("booking_amount_default", 100) or 100)

    def _notify(self, session: dict) -> None:
        """Best-effort WhatsApp confirmation; failures never break the call."""
        try:
            from .whatsapp import send_patient_confirmation  # lazy import

            appt = self.db.query(Appointment).filter(
                Appointment.tenant_id == self.tenant_id,
                Appointment.id == session.get("appointment_id")).first()
            if appt:
                send_patient_confirmation(self.db, self.tenant_id, appt)
        except Exception:
            pass

    # -- reschedule / cancel ---------------------------------------------------
    def _start_reschedule_cancel(self, session: dict, ext: BookingExtraction,
                                 text: str) -> dict:
        self._merge(session, ext)
        action = ext.intent if ext.intent in ("reschedule", "cancel") \
            else session.get("pending_action") or "reschedule"
        session["pending_action"] = action
        mobile = session.get("mobile")
        if not mobile:
            session["state"] = STATE_COLLECT
            return {"reply": "Apni purani booking dhoondhne ke liye apna 10-digit "
                             "mobile number batayein.",
                    "state": STATE_COLLECT, "action": "ask_mobile"}
        patient = (
            self.db.query(Patient)
            .filter(Patient.tenant_id == self.tenant_id, Patient.mobile == mobile)
            .first()
        )
        appt = None
        if patient:
            appt = (
                self.db.query(Appointment)
                .filter(Appointment.tenant_id == self.tenant_id,
                        Appointment.patient_id == patient.id,
                        Appointment.status == "CONFIRMED")
                .order_by(Appointment.slot_start.desc()).first()
            )
        if not appt:
            session["state"] = STATE_INTENT
            return {"reply": "Is mobile number par koi confirmed appointment nahi mili. "
                             "Reception se confirm karke batayenge.",
                    "state": STATE_INTENT, "action": "lookup_failed"}
        if patient and patient.name:
            session["patient_name"] = patient.name
        session["pending_appointment_id"] = appt.id
        session["old_appointment_id"] = appt.id
        doctor_name = self._doctor_name(appt.doctor_id)
        if action == "cancel":
            session["state"] = STATE_CONFIRM_BACK
            return {"reply": f"Aapki appointment {format_confirm_date(appt.slot_start)}, "
                             f"{self._fmt_time(appt.slot_start)}, Doctor {doctor_name} ke saath hai. "
                             "Kya ise cancel kar doon? Haan bolkar confirm karein.",
                    "state": STATE_CONFIRM_BACK, "action": "confirm_back"}
        session["state"] = STATE_COLLECT
        return {"reply": f"Aapki purani appointment {format_confirm_date(appt.slot_start)}, "
                         f"{self._fmt_time(appt.slot_start)} wali hai. "
                         "Nayi date aur samay batayein — jaise kal subah 10 baje.",
                "state": STATE_COLLECT, "action": "ask_datetime"}

    # -- FAQ: ONLY from clinic config (PRD Sec 7) ------------------------------
    def _answer_faq(self, session: dict, text: str) -> dict:
        low = text.lower()
        if re.search(r"fees|charges|kitna|kharcha", low):
            fees = self._cfg("fees_text", "")
            answer = f"Fees: {fees}." if fees else \
                "Fees ke baare mein reception se confirm karke batayenge."
        elif re.search(r"timing|kab khulta|kab band|band hota|kab tak|khula|opening|closing|kitne baje khulta", low):
            opening = self._cfg("opening_time", "")
            closing = self._cfg("closing_time", "")
            answer = (f"Clinic subah {opening} se {closing} tak khula rehta hai."
                      if opening and closing else
                      "Clinic ke timings reception se confirm karke batayenge.")
        elif re.search(r"address|kahan hai|location|pata", low):
            address = self._cfg("address", "")
            answer = f"Clinic ka address: {address}." if address else \
                "Address reception se confirm karke batayenge."
        elif re.search(r"namaste|hello|kaun|about|kya hai", low):
            greeting = self._cfg("greeting_text", "")
            answer = greeting if greeting else \
                "Clinic ke baare mein reception se confirm karke batayenge."
        else:
            answer = "Iske baare mein reception se confirm karke batayenge."
        return {"reply": answer + " Aur kuch poochna hai?",
                "state": STATE_INTENT, "action": "faq"}
