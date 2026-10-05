"""Local (no API) language detection for PMC posts: Marathi / Hindi / English.

Devanagari is shared by Marathi and Hindi, so the script alone can't tell them
apart. We score common function words instead. This is a fast first pass; when
Sarvam is configured its identify_language result overrides it.
"""
import re

_DEVA = re.compile(r"[\u0900-\u097F]")
_LATIN = re.compile(r"[A-Za-z]")

# Frequent function words / endings that are distinctive per language.
_MR = ["आहे", "आहेत", "मध्ये", "येथे", "करण्यात", "यांनी", "साठी", "नागरिकांनी", "होणार", "झाला", "झाली",
       "केले", "केली", "पुणे महानगरपालिका", "च्या", "ची ", "चा ", "ना ", "तर ", "नाही", "अशी", "असे"]
_HI = ["में", "है", "हैं", "के लिए", "किया", "किए", "की ", "का ", "को ", "से ", "नहीं", "गया", "गई", "हुआ",
       "हुई", "द्वारा", "पुणे नगर निगम", "कृपया", "रहेगा", "रहेगी"]


def detect_language(text: str) -> str:
    """Return 'mr', 'hi', 'en' or 'other'."""
    t = text or ""
    deva = len(_DEVA.findall(t))
    latin = len(_LATIN.findall(t))
    if deva == 0:
        return "en" if latin else "other"
    if latin > deva * 3:       # mostly English with a Devanagari hashtag or two
        return "en"
    mr = sum(t.count(w) for w in _MR)
    hi = sum(t.count(w) for w in _HI)
    # PMC is a Maharashtra body: Devanagari with no clear Hindi signal is far more likely Marathi.
    return "hi" if hi > mr else "mr"


LANG_LABELS = {"mr": "मराठी", "hi": "हिंदी", "en": "English", "other": "Other"}


def from_sarvam_code(code: str | None) -> str | None:
    c = (code or "").lower()
    if c.startswith("mr"):
        return "mr"
    if c.startswith("hi"):
        return "hi"
    if c.startswith("en"):
        return "en"
    return None
