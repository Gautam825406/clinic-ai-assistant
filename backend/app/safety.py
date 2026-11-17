"""Safety guardrails — PRD Sec 7 / Sec 11.4.

The assistant NEVER gives medical advice, a diagnosis, or a prescription.
Anything that sounds like an emergency stops the booking flow at once;
anything that asks for medical advice gets one fixed deflection line and
an offer to book the appointment or connect reception instead.
"""
import re

# --- Emergency patterns (Hindi + English + Hinglish mix) -------------------
# Each entry is a regex fragment, matched case-insensitively against the
# caller's text. Keep this list symptom-based, never diagnosis-based.
EMERGENCY_PATTERNS = [
    r"chest pain",
    r"seene?\s+me(?:in)?\s+dard",          # seene me dard / seene mein dard
    r"chhaati\s+me(?:in)?\s+dard",         # chhaati me dard
    r"dil\s+ka\s+(daura|doura|attack)",  # dil ka daura
    r"heart\s+attack",
    r"saans\s+(nahi|na)\s+aa\s+rah",   # saans nahi aa rahi/raha
    r"saans\s+ruk",                    # saans ruk rahi hai
    r"saans\s+lene\s+me(?:in)?\s+(takleef|dikat|dard)",
    r"breath\w*\s+(difficult|problem|short|stopped|can't|cannot|not)",
    r"can't\s+breathe",
    r"cannot\s+breathe",
    r"shortness\s+of\s+breath",
    r"difficulty\s+breathing",
    r"khoon\s+(beh|aa|nikal)",         # khoon beh raha / aa raha
    r"bleeding",
    r"blood\s+(coming|loss|vomit)",
    r"behosh",                         # behosh / behoshi
    r"behoshi",
    r"faint",
    r"unconscious",
    r"hosh\s+nahi",
    r"seizure",
    r"mirgi",
    r"daura\s+pad",                    # daura pad gaya (fit/seizure)
    r"fit\s+(aa|pad)",
    r"tez\s+dard",                     # tez dard (severe pain)
    r"bahut\s+(tez\s+)?dard",
    r"severe\s+pain",
    r"unbearable\s+pain",
    r"accident",
    r"chot\s+lag",
    r"jal\s+gaya",                     # burn
    r"burn\w*",
    r"poison",
    r"zeher",
    r"overdose",
    r"suicide",
    r"khud\s+ko\s+nuksan",
]

# --- Medical-advice patterns ----------------------------------------------
# Questions asking the assistant to diagnose, prescribe, dose, or judge
# how serious something is. These must deflect, never be answered.
ADVICE_PATTERNS = [
    r"kaun\s*si\s+dawa",               # kaunsi dawa / kaun si dawa
    r"kaun\s*si\s+dawai",
    r"kaunsi\s+dawai",
    r"kaunsi\s+medicine",
    r"dawa(i)?\s+(loon|lu|khaun|kha|lena|len|do|de)",
    r"dawai\s+(loon|lu|khaun|kha|lena|len|do|de)",
    r"which\s+medicine",
    r"which\s+tablet",
    r"what\s+medicine",
    r"medicine\s+(should|can|to)\s+(i\s+)?(take|use)",
    r"tablet\s+(loon|lu|khaun|lena|len|kaunsi|kaun\s*si)",
    r"ye\s+cancer\s+to\s+nahi",        # ye cancer to nahi
    r"cancer\s+to\s+nahi",
    r"is\s+this\s+cancer",
    r"could\s+this\s+be\s+cancer",
    r"diagnosis",
    r"diagnose",
    r"mujhe\s+kya\s+(hua|hai|bimari)",
    r"what('s|s)?\s+wrong\s+with\s+me",
    r"what\s+disease",
    r"dose",
    r"khurak",
    r"kitni\s+dawa",
    r"kitni\s+dose",
    r"serious\s+hai",
    r"is\s+it\s+serious",
    r"how\s+serious",
    r"gambhir\s+hai",
    r"kya\s+khana\s+chahiye",          # dietary/medical "what should I eat"
    r"kya\s+karna\s+chahiye",
    r"what\s+should\s+i\s+(do|eat|take)",
    r"ilaaj\s+(kya|kaun)",
    r"treatment\s+(kya|what|which|should)",
    r"report\s+ka\s+matlab",
    r"report\s+me(?:in)?\s+kya\s+hai",
    r"what\s+does\s+my\s+report\s+mean",
    r"injection\s+(loon|lu|lena|kaunsi)",
    r"dawa\s+ka\s+naam",
    r"prescri\w+",                     # prescribe / prescription
    r"antibiotic",
]

_EMERGENCY_RE = [re.compile(p, re.IGNORECASE) for p in EMERGENCY_PATTERNS]
_ADVICE_RE = [re.compile(p, re.IGNORECASE) for p in ADVICE_PATTERNS]


def detect_emergency(text: str) -> bool:
    """True when the text describes a medical emergency (PRD Sec 7).

    Checked FIRST, before any intent extraction or booking step.
    """
    if not text:
        return False
    return any(rx.search(text) for rx in _EMERGENCY_RE)


def detect_advice_request(text: str) -> bool:
    """True when the caller asks for medical advice / diagnosis / a dose.

    Checked right after the emergency check, before booking logic.
    """
    if not text:
        return False
    return any(rx.search(text) for rx in _ADVICE_RE)


def deflection_response(lang: str = "hinglish") -> str:
    """Fixed deflection line — never medical advice (PRD Sec 7).

    The doctor will tell the caller; the assistant can only book the
    appointment or connect reception.
    """
    if (lang or "").lower().startswith("en"):
        return (
            "I am sorry, I cannot give medical advice, a diagnosis, or suggest "
            "any medicine — only the doctor can tell you that. "
            "I can book your appointment, or connect you to reception."
        )
    return (
        "Maaf kijiye, main dawa, diagnosis ya koi medical salah nahi de sakta — "
        "ye sirf doctor hi bata sakte hain. "
        "Main aapki appointment book kar sakta hoon, ya reception se connect kar sakta hoon."
    )


def emergency_script(lang: str = "hinglish") -> str:
    """Emergency script — booking stops here (PRD Sec 7).

    Tells the caller to go to the nearest hospital / call 112 at once.
    Callers of this function should also flag the clinic for an alert
    (DialogueEngine sets session['emergency_alert'] = True).
    """
    if (lang or "").lower().startswith("en"):
        return (
            "This sounds like an emergency — please do not wait for an appointment. "
            "Go to the nearest hospital right now, or call 112 immediately. "
            "I am alerting the clinic as well."
        )
    return (
        "Ye emergency lag rahi hai — appointment ka intezaar mat kijiye. "
        "Turant nazdeeki hospital jaiye, ya 112 par call kijiye. "
        "Main clinic ko bhi alert kar raha hoon."
    )
