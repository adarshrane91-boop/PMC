"""PMC civic-news classification with multilingual hashtag/keyword matching.

Fast path: hashtags and civic keywords (English/Hindi/Marathi).

Optional Sarvam AI path: when SARVAM_API_KEY is configured, Devanagari/Indic
posts are language-detected and translated to English before a second pass.

The original post text is always preserved.
"""

import hashlib
import json
import logging
import os
import re
from pathlib import Path

from lang_utils import detect_language, from_sarvam_code


logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Small .env loader
# ---------------------------------------------------------------------------

def _load_dotenv():
    """Load simple KEY=VALUE entries from .env.

    Real environment variables always win.
    """

    f = BASE_DIR / ".env"

    if not f.exists():
        return

    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except Exception:
        logger.warning("Could not read .env", exc_info=True)
        return

    for line in lines:
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        if "=" not in line:
            continue

        k, v = line.split("=", 1)

        k = k.strip()
        v = v.strip().strip('"').strip("'")

        if k and v and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


# ---------------------------------------------------------------------------
# Sarvam limits
# ---------------------------------------------------------------------------

# Sarvam Mayura v1 accepts a maximum of 1000 characters for translation.
SARVAM_TRANSLATION_MAX_CHARS = 1000

# Keep the larger limit for the chat-analysis request.
SARVAM_ANALYSIS_MAX_CHARS = 2000


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------

CATEGORIES = [
    "fire",
    "traffic",
    "road_damage",
    "garbage",
    "water_supply",
    "flood",
    "drainage",
    "power_streetlight",
    "environment",
    "health",
    "animal",
    "heritage",
    "development",
    "transport",
    "encroachment",
    "civic_services",
    "events_awareness",
    "other",
]


# ---------------------------------------------------------------------------
# Category metadata
# ---------------------------------------------------------------------------

CATEGORY_META = {
    "fire": (
        "Fire / Emergency",
        "अग्निशमन / आपत्कालीन",
        [
            "#Fire",
            "#FireIncident",
            "#FireEmergency",
            "#FireDepartment",
            "#PMCFireDept",
            "#FireSafety",
            "#Emergency",
            "#Rescue",
            "#FireControl",
            "#FireBrigade",
            "#आग",
            "#आगीचीघटना",
            "#अग्निशमन",
            "#अग्निशमनदल",
            "#अग्निशमनकेंद्र",
            "#आगीवरनियंत्रण",
            "#आपत्कालीन",
            "#बचावकार्य",
        ],
    ),

    "traffic": (
        "Traffic",
        "वाहतूक",
        [
            "#Traffic",
            "#PuneTraffic",
            "#वाहतूककोंडी",
            "#वाहतूकव्यवस्था",
            "#TrafficManagement",
            "#TrafficUpdate",
            "#TrafficAlert",
            "#TrafficControl",
            "#RoadSafety",
            "#TrafficPolice",
            "#TrafficDiversion",
            "#Diversion",
            "#वाहतूक",
            "#वाहतूककोंडी",
            "#वाहतूकव्यवस्था",
            "#वाहतूकनियंत्रण",
            "#वाहतूकबदल",
            "#वाहतूकवळविणे",
            "#रहदारी",
            "#रस्तेसुरक्षा",
        ],
    ),

    "road_damage": (
        "Road / Damage",
        "रस्ता / नुकसान",
        [
            "#Road",
            "#RoadDamage",
            "#RoadRepair",
            "#RoadDevelopment",
            "#RoadWork",
            "#RoadConstruction",
            "#Infrastructure",
            "#PuneInfrastructure",
            "#PuneDevelopment",
            "#Pothole",
            "#Potholes",
            "#RoadSafety",
            "#RoadPotHoles",
            "#RoadRepairWork",
            "#workinprogress",
            "#KatrajKondhwaRoad",
            "#KatrajKondhwa",
            "#रस्ता",
            "#रस्ताविकास",
            "#रस्तेदुरुस्ती",
            "#रस्तेकाम",
            "#रस्तेबांधकाम",
            "#रस्त्यांचीस्थिती",
            "#खड्डे",
            "#खड्डेमुक्तपुणे",
            "#पायाभूतसुविधा",
        ],
    ),

    "garbage": (
        "Garbage / Waste",
        "कचरा / घनकचरा",
        [
            "#Garbage",
            "#Waste",
            "#SolidWaste",
            "#WasteManagement",
            "#GarbageCollection",
            "#CleanPune",
            "#SwachhPune",
            "#Swachhta",
            "#SwachhBharat",
            "#SwachhtaHiSeva",
            "#CleanCity",
            "#Sanitation",
            "#CleanlingWorkers",
            "#DeepCleanDrive",
            "#SwachhSarvekshan",
            "#GaneshVisarjan",
            "#कचरा",
            "#कचराव्यवस्थापन",
            "#घनकचराव्यवस्थापन",
            "#कचरासंकलन",
            "#स्वच्छता",
            "#स्वच्छपुणे",
            "#स्वच्छताहीसेवा",
            "#स्वच्छशहर",
            "#घनकचरा",
        ],
    ),

    "flood": (
        "Flood / Waterlogging",
        "पूर / पाणी साचणे",
        [
            "#Flood",
            "#FloodAlert",
            "#Flooding",
            "#WaterLogging",
            "#Waterlogging",
            "#HeavyRain",
            "#RainAlert",
            "#Rainfall",
            "#Monsoon",
            "#PuneRains",
            "#पूर",
            "#पुरस्थिती",
            "#पाणी",
            "#पाणीसाचणे",
            "#पाणीभरले",
            "#अतिवृष्टी",
            "#मुसळधारपाऊस",
            "#पावसाळा",
            "#पावसाचीस्थिती",
        ],
    ),

    "water_supply": (
        "Water Supply",
        "पाणीपुरवठा",
        [
            "#WaterSupply",
            "#Water",
            "#WaterShortage",
            "#WaterCrisis",
            "#WaterPipeline",
            "#WaterLeakage",
            "#WaterManagement",
            "#पाणीपुरवठा",
            "#पाणीटंचाई",
            "#पाणीकपात",
            "#पाण्याचीसमस्या",
            "#पाईपलाईन",
            "#पाण्याचीगळती",
            "#जलव्यवस्थापन",
        ],
    ),

    "environment": (
        "Environment",
        "पर्यावरण",
        [
            "#Environment",
            "#GreenPune",
            "#PuneEnvironment",
            "#Tree",
            "#TreePlantation",
            "#TreeCare",
            "#GreenCity",
            "#AirQuality",
            "#AQI",
            "#Pollution",
            "#Climate",
            "#पर्यावरण",
            "#हरितपुणे",
            "#वृक्षारोपण",
            "#झाडे",
            "#झाडांचीसुरक्षा",
            "#वायुप्रदूषण",
            "#हवेचीगुणवत्ता",
            "#प्रदूषण",
        ],
    ),

    "health": (
        "Health",
        "आरोग्य",
        [
            "#Health",
            "#Healthcare",
            "#PublicHealth",
            "#HealthCamp",
            "#MedicalCamp",
            "#Vaccination",
            "#DiseasePrevention",
            "#HealthAwareness",
            "#DogBite",
            "#Rabies",
            "#RabiesAwareness",
            "#RabiesPrevention",
            "#आरोग्य",
            "#आरोग्यसेवा",
            "#सार्वजनिकआरोग्य",
            "#आरोग्यशिबिर",
            "#वैद्यकीयशिबिर",
            "#लसीकरण",
            "#आरोग्यजागरूकता",
            "#कुत्राचावा",
            "#रेबीज",
        ],
    ),

    "animal": (
        "Animals",
        "प्राणी",
        [
            "#Animal",
            "#StrayAnimals",
            "#StrayDogs",
            "#Dog",
            "#DogBite",
            "#AnimalCare",
            "#AnimalRescue",
            "#Rabies",
            "#भटक्याकुत्रे",
            "#भटकाप्राणी",
            "#प्राणीकल्याण",
            "#प्राणीसुरक्षा",
            "#कुत्राचावा",
            "#प्राण्यांचेबचावकार्य",
        ],
    ),

    "heritage": (
        "Heritage",
        "वारसा",
        [
            "#Heritage",
            "#PuneHeritage",
            "#PuneHistory",
            "#HeritageConservation",
            "#ShaniwarWada",
            "#HistoricPune",
            "#HeritagePune",
            "#वारसा",
            "#ऐतिहासिकवारसा",
            "#पुण्याचावारसा",
            "#वारसास्थळ",
            "#ऐतिहासिकस्थळ",
            "#वारसाजतन",
        ],
    ),

    "development": (
        "Development / Infrastructure",
        "विकास / पायाभूत सुविधा",
        [
            "#Development",
            "#PuneDevelopment",
            "#PuneInfrastructure",
            "#Infrastructure",
            "#UrbanDevelopment",
            "#CivicDevelopment",
            "#SmartCity",
            "#CityDevelopment",
            "#विकास",
            "#पुणेविकास",
            "#नागरीविकास",
            "#शहरविकास",
            "#पायाभूतसुविधा",
            "#नागरीसुविधा",
            "#विकासकामे",
        ],
    ),

    "transport": (
        "Public Transport",
        "सार्वजनिक वाहतूक",
        [
            "#Transport",
            "#PublicTransport",
            "#Bus",
            "#PMCBus",
            "#PMPML",
            "#Metro",
            "#PuneMetro",
            "#PublicTransit",
            "#सार्वजनिकवाहतूक",
            "#बससेवा",
            "#पुणेबस",
            "#वाहतूकसेवा",
            "#मेट्रो",
        ],
    ),

    "power_streetlight": (
        "Power / Street Lights",
        "वीज / पथदिवे",
        [
            "#StreetLight",
            "#StreetLighting",
            "#Electricity",
            "#Power",
            "#PowerOutage",
            "#Lighting",
            "#पथदिवे",
            "#पथदिवादुरुस्ती",
            "#वीज",
            "#वीजपुरवठा",
            "#वीजखंडित",
            "#दिवेदुरुस्ती",
        ],
    ),

    "encroachment": (
        "Encroachment / Construction",
        "अतिक्रमण / बांधकाम",
        [
            "#Encroachment",
            "#IllegalConstruction",
            "#UnauthorizedConstruction",
            "#Demolition",
            "#BuildingPermission",
            "#Construction",
            "#अतिक्रमण",
            "#अनधिकृतबांधकाम",
            "#बेकायदेशीरबांधकाम",
            "#अतिक्रमणविरोधी",
            "#बांधकाम",
            "#पाडकाम",
        ],
    ),

    "civic_services": (
        "Civic Services",
        "नागरी सेवा",
        [
            "#CivicServices",
            "#CivicWork",
            "#MunicipalServices",
            "#PublicServices",
            "#PMCServices",
            "#CivicBody",
            "#CitizenServices",
            "#नागरीसेवा",
            "#नागरीकामे",
            "#महानगरपालिका",
            "#नागरीसुविधा",
            "#नागरिकसेवा",
            "#सार्वजनिकसेवा",
        ],
    ),

    "events_awareness": (
        "Events / Awareness",
        "कार्यक्रम / जनजागृती",
        [
            "#Event",
            "#PuneEvent",
            "#PublicEvent",
            "#Awareness",
            "#Campaign",
            "#CivicAwareness",
            "#PublicAwareness",
            "#कार्यक्रम",
            "#जनजागृती",
            "#मोहीम",
            "#नागरिकजागृती",
            "#जनजागरण",
        ],
    ),

    "drainage": (
        "Drainage / Sewage",
        "गटार / सांडपाणी",
        [
            "#Drainage",
            "#Sewerage",
            "#Sewage",
            "#StormWater",
            "#Drain",
            "#गटार",
            "#नाला",
            "#सांडपाणी",
            "#जलनिस्सारण",
        ],
    ),

    "other": (
        "General PMC Update",
        "सामान्य PMC अपडेट",
        [
            "#PMC",
            "#PMCPune",
            "#PuneMC",
            "#PuneMunicipalCorporation",
            "#PuneMunicipal",
            "#PuneMahanagarpalika",
            "#PuneCity",
            "#Pune",
            "#PMCCare",
            "#PMCUpdates",
            "#PMCUpdate",
            "#PuneUpdates",
            "#PuneNews",
            "#PuneDevelopment",
            "#पुणेमहानगरपालिका",
            "#पुणेमनपा",
            "#पुणेशहर",
            "#पुणेविकास",
            "#पुणेबातम्या",
            "#मनपा",
        ],
    ),
}


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """Normalize hashtag/keyword forms."""

    return re.sub(
        r"[^\w\u0900-\u097F]+",
        "",
        (s or "").casefold(),
    )


# ---------------------------------------------------------------------------
# Build hashtag rules
# ---------------------------------------------------------------------------

RULES = {}

for cat, (_, _, tags) in CATEGORY_META.items():
    RULES[cat] = {_norm(x) for x in tags}


# ---------------------------------------------------------------------------
# Phrase rules
# ---------------------------------------------------------------------------

PHRASES = {
    "fire": [
        "fire incident",
        "fire broke",
        "fire control",
        "अग्निशमन",
        "आग लागली",
        "आग नियंत्रण",
    ],

    "traffic": [
        "traffic",
        "congestion",
        "traffic diversion",
        "वाहतूक",
        "वाहतूक कोंडी",
    ],

    "road_damage": [
        "pothole",
        "road damage",
        "road repair",
        "खड्डा",
        "रस्ता दुरुस्ती",
        "रस्त्याची अवस्था",
    ],

    "garbage": [
        "garbage",
        "solid waste",
        "waste collection",
        "कचरा",
        "घनकचरा",
        "स्वच्छता",
    ],

    "water_supply": [
        "water supply",
        "water shortage",
        "pipeline leak",
        "पाणीपुरवठा",
        "पाणीटंचाई",
        "पाण्याची गळती",
    ],

    "flood": [
        "waterlogging",
        "flood",
        "heavy rain",
        "पाणी साच",
        "पूर",
        "अतिवृष्टी",
        "मुसळधार पाऊस",
    ],

    "drainage": [
        "drainage",
        "sewer",
        "sewage",
        "storm water",
        "गटार",
        "नाला",
        "सांडपाणी",
    ],

    "power_streetlight": [
        "street light",
        "streetlight",
        "power cut",
        "electricity",
        "पथदिवा",
        "वीजपुरवठा",
        "वीज खंडित",
    ],

    "environment": [
        "environment",
        "air quality",
        "pollution",
        "tree plantation",
        "पर्यावरण",
        "वृक्षारोपण",
        "प्रदूषण",
    ],

    "health": [
        "health camp",
        "vaccination",
        "rabies",
        "public health",
        "आरोग्य",
        "लसीकरण",
        "रेबीज",
    ],

    "animal": [
        "stray dog",
        "animal rescue",
        "भटके कुत्रे",
        "भटक्या कुत्र्य",
        "प्राण्यांचे",
    ],

    "heritage": [
        "heritage",
        "shaniwar wada",
        "वारसा",
        "ऐतिहासिक",
    ],

    "development": [
        "development",
        "infrastructure",
        "road development",
        "विकास",
        "पायाभूत सुविधा",
        "विकासकामे",
    ],

    "transport": [
        "public transport",
        "bus service",
        "metro",
        "सार्वजनिक वाहतूक",
        "बस सेवा",
        "मेट्रो",
    ],

    "encroachment": [
        "encroachment",
        "illegal construction",
        "unauthorized construction",
        "अतिक्रमण",
        "अनधिकृत बांधकाम",
    ],

    "civic_services": [
        "civic services",
        "municipal services",
        "citizen services",
        "नागरी सेवा",
        "नागरिक सेवा",
    ],

    "events_awareness": [
        "awareness",
        "campaign",
        "जनजागृती",
        "मोहीम",
        "कार्यक्रम",
    ],
}


# ---------------------------------------------------------------------------
# Hashtag extraction
# ---------------------------------------------------------------------------

def extract_hashtags(text: str):
    return re.findall(
        r"#[\w\u0900-\u097F]+",
        text or "",
    )


# ---------------------------------------------------------------------------
# Keyword classifier
# ---------------------------------------------------------------------------

def keyword_classify(text: str):
    tags = {_norm(x) for x in extract_hashtags(text)}

    lower = (text or "").casefold()

    scores = {
        cat: 0
        for cat in CATEGORIES
    }

    matched = {
        cat: []
        for cat in CATEGORIES
    }

    for cat, words in RULES.items():

        if cat == "other":
            continue

        # Hashtag evidence
        for tag in tags:
            if tag in words:
                scores[cat] += 4
                matched[cat].append("#" + tag)

        # Phrase evidence
        for phrase in PHRASES.get(cat, []):
            if phrase.casefold() in lower:
                scores[cat] += 2
                matched[cat].append(phrase)

    # Generic PMC/Pune tags should not overwhelm
    # an issue-specific category.
    scores["other"] += min(
        1,
        sum(
            1
            for t in tags
            if t in RULES["other"]
        ),
    )

    best = max(
        scores,
        key=scores.get,
    )

    if scores[best] == 0:
        best = "other"

    return (
        best,
        matched.get(best, []),
        scores,
    )


# ---------------------------------------------------------------------------
# Sarvam client
# ---------------------------------------------------------------------------

def _sarvam_client():
    key = os.environ.get("SARVAM_API_KEY")

    if not key:
        return None

    try:
        from sarvamai import SarvamAI

        return SarvamAI(
            api_subscription_key=key
        )

    except Exception as exc:
        logger.warning(
            "Sarvam AI unavailable: %s",
            exc,
        )
        return None


# ---------------------------------------------------------------------------
# Sarvam cache
# ---------------------------------------------------------------------------

CACHE_PATH = (
    BASE_DIR
    / "data"
    / "sarvam_cache.json"
)

_cache = None


def _cache_load():
    global _cache

    if _cache is None:

        try:
            _cache = json.loads(
                CACHE_PATH.read_text(
                    encoding="utf-8"
                )
            )

            if not isinstance(_cache, dict):
                _cache = {}

        except Exception:
            _cache = {}

    return _cache


def _cache_save():
    try:

        CACHE_PATH.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        CACHE_PATH.write_text(
            json.dumps(
                _cache,
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    except Exception:
        logger.warning(
            "Could not write Sarvam cache",
            exc_info=True,
        )


def _key(text: str) -> str:
    return hashlib.sha1(
        (text or "")
        .strip()
        .encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Sarvam analysis prompt
# ---------------------------------------------------------------------------

ANALYZE_PROMPT = (
    "You read official Pune Municipal Corporation (PMC) "
    "social-media posts written in Marathi, Hindi or English. "

    "Reply with ONE JSON object and nothing else, "
    "with exactly these keys:\n"

    '"category": one of '
    + ", ".join(CATEGORIES)
    + ";\n"

    '"location": the Pune locality, ward, road or landmark '
    'the post is about, written in English letters '
    '(e.g. "Kothrud", "Baner", "Katraj"), '
    'or "" if the post names none;\n'

    '"places": a JSON list (max 5) of every SPECIFIC road, '
    'chowk, junction, landmark or locality named in the post, '
    'in English letters, most specific first '
    '(e.g. ["Seven Loves Chowk", '
    '"Sant Kabir Chowk", "Kasba Peth"]); '
    '[] if none;\n'

    '"summary_en": one plain-English sentence '
    "summarising the post.\n"

    "Never invent a location that is not in the post "
    "or its hashtags."
)


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

def _parse_json_object(raw: str):
    """Extract the first JSON object from model output."""

    if not raw:
        return None

    # Remove optional <think>...</think> blocks.
    raw = re.sub(
        r"<think>.*?</think>",
        "",
        raw,
        flags=re.S,
    )

    # Remove common markdown code fences.
    raw = raw.replace("```json", "")
    raw = raw.replace("```", "")

    # Find JSON object.
    m = re.search(
        r"\{.*\}",
        raw,
        flags=re.S,
    )

    if not m:
        return None

    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Sarvam analyzer
# ---------------------------------------------------------------------------

def sarvam_analyze(
    text: str,
    local_lang: str,
):
    """Ask Sarvam to read the post.

    Returns:
        dict or None

    The function never raises. Ingestion continues if Sarvam fails.
    """

    client = _sarvam_client()

    if client is None:
        return None

    if not (text or "").strip():
        return None

    cache = _cache_load()

    k = _key(text)

    # Reuse completed analysis.
    if (
        k in cache
        and "places" in cache[k]
    ):
        return cache[k]

    out = {
        "language": None,
        "translated_text": None,
        "category": None,
        "location": None,
        "summary_en": None,
        "places": [],
    }

    try:

        # ---------------------------------------------------------------
        # Marathi / Hindi language detection + translation
        # ---------------------------------------------------------------

        if local_lang in ("mr", "hi"):

            # Let Sarvam confirm Marathi vs Hindi.
            try:

                lid = client.text.identify_language(
                    input=text[
                        :SARVAM_TRANSLATION_MAX_CHARS
                    ]
                )

                out["language"] = from_sarvam_code(
                    getattr(
                        lid,
                        "language_code",
                        None,
                    )
                )

            except Exception:
                logger.warning(
                    "Sarvam identify_language failed",
                    exc_info=True,
                )

            src = {
                "mr": "mr-IN",
                "hi": "hi-IN",
            }.get(
                out["language"] or local_lang,
                "auto",
            )

            # -----------------------------------------------------------
            # IMPORTANT:
            # Mayura v1 has a 1000-character input limit.
            # -----------------------------------------------------------

            try:

                tr = client.text.translate(
                    input=text[
                        :SARVAM_TRANSLATION_MAX_CHARS
                    ],
                    source_language_code=src,
                    target_language_code="en-IN",
                    model="mayura:v1",
                    mode="modern-colloquial",
                )

                out["translated_text"] = getattr(
                    tr,
                    "translated_text",
                    None,
                )

            except Exception:
                logger.warning(
                    "Sarvam translate failed",
                    exc_info=True,
                )

        # ---------------------------------------------------------------
        # Sarvam chat analysis
        # ---------------------------------------------------------------

        try:

            resp = client.chat.completions(
                model=os.environ.get(
                    "SARVAM_CHAT_MODEL",
                    "sarvam-105b",
                ),

                messages=[
                    {
                        "role": "system",
                        "content": ANALYZE_PROMPT,
                    },
                    {
                        "role": "user",
                        "content": text[
                            :SARVAM_ANALYSIS_MAX_CHARS
                        ],
                    },
                ],

                temperature=0.1,
                max_tokens=500,
            )

            content = ""

            try:
                content = (
                    resp
                    .choices[0]
                    .message
                    .content
                )
            except Exception:
                content = ""

            data = (
                _parse_json_object(content)
                or {}
            )

            # -----------------------------------------------------------
            # Category
            # -----------------------------------------------------------

            cat = str(
                data.get("category")
                or ""
            ).strip().lower()

            if cat in CATEGORIES:
                out["category"] = cat

            # -----------------------------------------------------------
            # Location
            # -----------------------------------------------------------

            loc = str(
                data.get("location")
                or ""
            ).strip()

            if (
                loc
                and loc.lower()
                not in {
                    "none",
                    "n/a",
                    "unknown",
                    "pune",
                }
            ):
                out["location"] = loc[:80]

            # -----------------------------------------------------------
            # English summary
            # -----------------------------------------------------------

            summary = str(
                data.get("summary_en")
                or ""
            ).strip()

            if summary:
                out["summary_en"] = summary

            # -----------------------------------------------------------
            # Places
            # -----------------------------------------------------------

            raw_places = (
                data.get("places")
                or []
            )

            if isinstance(
                raw_places,
                str,
            ):
                raw_places = re.split(
                    r"[;,]",
                    raw_places,
                )

            if not isinstance(
                raw_places,
                list,
            ):
                raw_places = []

            places = []

            for place in raw_places:

                value = str(
                    place
                ).strip()

                if not value:
                    continue

                if value.lower() in {
                    "none",
                    "n/a",
                    "unknown",
                    "pune",
                }:
                    continue

                places.append(
                    value[:80]
                )

                if len(places) >= 5:
                    break

            out["places"] = places

        except Exception:
            logger.warning(
                "Sarvam chat analysis failed",
                exc_info=True,
            )

    except Exception:
        logger.exception(
            "Sarvam analysis failed"
        )
        return None

    # Only cache real answers.
    # Transient API failures are retried on next ingest.
    if (
        out["category"]
        or out["summary_en"]
    ):
        cache[k] = out
        _cache_save()

    return out


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------

def classify_post(text: str):
    """Classify one civic post.

    Processing order:

    1. Hashtags / phrases decide first.
    2. Sarvam optionally detects/translates Indic text.
    3. Translated text is checked against the same deterministic rules.
    4. Sarvam category is used when keyword evidence is weak.
    5. Original text is never altered.
    """

    text = text or ""

    # ---------------------------------------------------------------
    # Deterministic classification
    # ---------------------------------------------------------------

    category, matched, scores = keyword_classify(
        text
    )

    # ---------------------------------------------------------------
    # Language detection
    # ---------------------------------------------------------------

    try:
        local_lang = detect_language(text)
    except Exception:
        local_lang = "en"

    # ---------------------------------------------------------------
    # Optional Sarvam analysis
    # ---------------------------------------------------------------

    ai = sarvam_analyze(
        text,
        local_lang,
    )

    language = (
        (ai or {}).get("language")
        or local_lang
    )

    translated = (
        (ai or {}).get("translated_text")
    )

    location_hint = (
        (ai or {}).get("location")
    )

    summary_en = (
        (ai or {}).get("summary_en")
        or translated
    )

    used_ai = False

    # ---------------------------------------------------------------
    # Re-check translated English against keyword rules
    # ---------------------------------------------------------------

    if translated:

        (
            tr_cat,
            tr_matched,
            tr_scores,
        ) = keyword_classify(
            translated
        )

        if (
            tr_scores.get(tr_cat, 0)
            > scores.get(category, 0)
        ):
            category = tr_cat

            matched = list(
                dict.fromkeys(
                    matched
                    + tr_matched
                )
            )

    # ---------------------------------------------------------------
    # Use Sarvam category only when deterministic
    # evidence is weak.
    # ---------------------------------------------------------------

    ai_category = (
        (ai or {}).get("category")
    )

    if (
        ai_category
        and scores.get(category, 0) < 4
    ):

        if (
            ai_category != "other"
            or category == "other"
        ):
            category = ai_category
            used_ai = True

    # ---------------------------------------------------------------
    # Category labels
    # ---------------------------------------------------------------

    (
        label_en,
        label_native,
        _,
    ) = CATEGORY_META.get(
        category,
        CATEGORY_META["other"],
    )

    # ---------------------------------------------------------------
    # Matched terms
    # ---------------------------------------------------------------

    terms = list(
        matched[:12]
    )

    if used_ai:
        terms.append(
            "Sarvam AI"
        )

    # ---------------------------------------------------------------
    # Final result
    # ---------------------------------------------------------------

    return {
        "category": category,

        "category_label": label_en,

        "category_label_native": label_native,

        "matched_terms": terms,

        "language": language,

        "lang": (
            language
            if language in (
                "mr",
                "hi",
                "en",
            )
            else local_lang
        ),

        "translated_text": translated,

        "summary_en": summary_en,

        "location_hint": location_hint,

        "places": (
            (ai or {}).get("places")
            or []
        ),

        "hashtags": extract_hashtags(
            text
        ),
    }


# ---------------------------------------------------------------------------
# Backwards-compatible simple classifier
# ---------------------------------------------------------------------------

def classify(
    text: str,
    use_llm_fallback: bool = True,
) -> str:
    """Return only the category name.

    Kept for compatibility with existing pipeline code.
    """

    return classify_post(
        text
    )["category"]