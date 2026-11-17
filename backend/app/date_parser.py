"""Deterministic Hinglish date/time parser (PRD Sec 11.2).

Date/time maths is NEVER left to the LLM: hints like "kal shaam", "parso
5 baje", "Monday ko" are resolved here, in IST, with fixed rules.
In booking speech "kal" means tomorrow (not yesterday) and "parso" means
the day after tomorrow.
"""
import re
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")

WEEKDAYS_EN = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
WEEKDAYS_HI = {
    "somvar": 0, "somvaar": 0, "mangalvar": 1, "mangalvaar": 1,
    "budhvar": 2, "budhvaar": 2, "guruvar": 3, "guruwar": 3,
    "shukravar": 4, "shukrawar": 4, "shanivar": 5, "shaniwar": 5,
    "ravivar": 6, "raviwar": 6,
}
MONTHS_EN = [
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
]
MONTHS_SHORT = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _norm(text: str) -> str:
    return text.translate(_DEVANAGARI).lower().strip()


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_date_hint(text: str, ref: date | None = None) -> date | None:
    """Resolve a date hint to a date. Returns None when it cannot be resolved."""
    ref = ref or now_ist().date()
    t = _norm(text)
    if re.search(r"\baaj\b", t):
        return ref
    if re.search(r"\bparso\b|\bday after tomorrow\b", t):
        return ref + timedelta(days=2)
    if re.search(r"\bkal\b|\btomorrow\b", t):
        return ref + timedelta(days=1)
    if re.search(r"\bnarso\b", t):  # day after parso, rarely used
        return ref + timedelta(days=3)
    # explicit day-month: "19 november", "19 nov", "19/11", "19-11-2026"
    m = re.search(r"(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        year = int(m.group(3)) if m.group(3) else ref.year
        if year < 100:
            year += 2000
        try:
            out = date(year, mo, d)
            if out < ref and not m.group(3):
                out = date(year + 1, mo, d)
            return out
        except ValueError:
            return None
    for i, name in enumerate(MONTHS_EN):
        m = re.search(rf"(\d{{1,2}})\s+{name}", t)
        if m:
            try:
                out = date(ref.year, i + 1, int(m.group(1)))
                if out < ref:
                    out = date(ref.year + 1, i + 1, int(m.group(1)))
                return out
            except ValueError:
                return None
    for i, name in enumerate(MONTHS_SHORT):
        m = re.search(rf"(\d{{1,2}})\s+{name}\b", t)
        if m:
            try:
                out = date(ref.year, i + 1, int(m.group(1)))
                if out < ref:
                    out = date(ref.year + 1, i + 1, int(m.group(1)))
                return out
            except ValueError:
                return None
    # weekday: "monday", "agle monday" (next), plain weekday = next occurrence
    for i, name in enumerate(WEEKDAYS_EN):
        if re.search(rf"\b{name}\b", t):
            delta = (i - ref.weekday()) % 7
            if delta == 0:
                delta = 7 if "agle" in t or "next" in t else 0
            return ref + timedelta(days=delta)
    for name, i in WEEKDAYS_HI.items():
        if name in t:
            delta = (i - ref.weekday()) % 7
            return ref + timedelta(days=delta if delta else 7)
    return None


def parse_time_hint(text: str) -> time | None:
    """Resolve a time hint to a time. Period words decide AM/PM."""
    t = _norm(text)
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(baje)?", t)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if hour > 23 or minute > 59:
        return None
    evening = any(w in t for w in ("shaam", "sham", "evening", "raat", "dopahar", "afternoon"))
    morning = any(w in t for w in ("subah", "morning", "savera"))
    if ampm == "pm" or (evening and hour < 12):
        hour = hour + 12 if hour < 12 else hour
    elif ampm == "am":
        hour = 0 if hour == 12 else hour
    elif morning and hour == 12:
        hour = 0
    if hour == 24:
        hour = 0
    try:
        return time(hour, minute)
    except ValueError:
        return None


def parse_datetime_hint(text: str, ref: date | None = None) -> datetime | None:
    """Combined date + time hint, e.g. 'kal shaam 4:30' -> naive IST datetime."""
    ref = ref or now_ist().date()
    d = parse_date_hint(text, ref)
    tm = parse_time_hint(text)
    if d is None or tm is None:
        return None
    return datetime.combine(d, tm)


def weekday_name(d: date) -> str:
    return WEEKDAYS_EN[d.weekday()].capitalize()


def format_confirm_date(dt: datetime, ref: date | None = None) -> str:
    """'kal, Thursday, 19 November' style confirm-back (PRD Sec 7)."""
    ref = ref or now_ist().date()
    rel = ""
    if dt.date() == ref:
        rel = "aaj, "
    elif dt.date() == ref + timedelta(days=1):
        rel = "kal, "
    elif dt.date() == ref + timedelta(days=2):
        rel = "parso, "
    return f"{rel}{weekday_name(dt.date())}, {dt.day} {MONTHS_EN[dt.month - 1].capitalize()}"
