
"""
PMC Civic Signal ingestion pipeline.

Runs every configured source, classifies each post, assigns it to a ward
when a reliable ward/location can be identified, and keeps PMC-wide reports
when no specific ward can be identified.

Storage:
    data/live_reports.json

Location precision:
    exact   -> post supplied real coordinates
    ward    -> location/ward identified from text or operator hint
    citywide -> PMC-related report but no specific ward could be identified

Citywide reports are NOT assigned to an arbitrary ward.

Instagram:
    Public Instagram discovery is handled by SocialCrawl in sources.py.
    SocialCrawl records are normalized before reaching this pipeline.
"""

import hashlib
import json
import logging
import math
import os
import re

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from classify import classify_post
from wards_gis import get_index
from ingestion.sources import MockSource, all_sources
from ingestion.geocode import geocode_place

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent

LIVE_REPORTS_PATH = (
    BASE_DIR
    / "data"
    / "live_reports.json"
)

ALIASES_PATH = (
    BASE_DIR
    / "data"
    / "location_ward_aliases.json"
)


# ============================================================================
# LOCATION / WARD ALIASES
# ============================================================================

try:

    LOCATION_WARD_ALIASES = json.loads(
        ALIASES_PATH.read_text(
            encoding="utf-8"
        )
    )

except Exception:

    LOCATION_WARD_ALIASES = {}


# ============================================================================
# GENERIC WARD NAME PARTS
# ============================================================================

GENERIC_NAME_PARTS = {
    "nagar",
    "gaon",
    "wadi",
    "colony",
    "road",
    "peth",
    "park",
    "chowk",
    "vasahat",
    "gaothan",

    "pune",
    "east",
    "west",
    "north",
    "south",
    "university",
    "savitribai",
    "phule",
    "municipal",
    "corporation",
    "city",
    "sheri",
    "chawl",
}


# ============================================================================
# TEXT NORMALISATION
# ============================================================================

def _normalise_text(
    value: str,
) -> str:
    """
    Normalise text for matching.

    Keeps Latin + Devanagari characters.
    """

    return re.sub(
        r"[^\w\u0900-\u097F]+",
        "",
        (value or "").casefold(),
    )


def _compact_text(
    value: str,
) -> str:

    return re.sub(
        r"[^a-z0-9\u0900-\u097F]",
        "",
        (value or "").casefold(),
    )


# ============================================================================
# WARD MATCHING
# ============================================================================

def _match_ward_by_name(
    text: str,
    index,
) -> dict | None:
    """
    Find a PMC ward from a location/ward name in text.

    Matching order:
        1. Explicit operator-configured aliases
        2. Actual ward-name parts

    Returns:
        ward dictionary or None
    """

    if not text:
        return None

    t = str(
        text
    ).casefold()

    compact = _compact_text(
        t
    )

    # ------------------------------------------------------------------
    # 1. Explicit location aliases
    # ------------------------------------------------------------------

    aliases = sorted(
        LOCATION_WARD_ALIASES.items(),
        key=lambda x: len(
            str(x[0])
        ),
        reverse=True,
    )

    for alias, ward_id in aliases:

        alias_text = (
            str(alias)
            .casefold()
            .strip()
        )

        if not alias_text:
            continue

        compact_alias = _compact_text(
            alias_text
        )

        try:

            matched = bool(
                re.search(
                    r"(?<![\w\u0900-\u097F])"
                    + re.escape(
                        alias_text
                    )
                    + r"(?![\w\u0900-\u097F])",
                    t,
                )
            )

            if (
                not matched
                and compact_alias
            ):

                matched = (
                    compact_alias
                    in compact
                )

            if not matched:
                continue

            ward = next(
                (
                    x
                    for x in index.wards
                    if x["id"]
                    == int(ward_id)
                ),
                None,
            )

            if ward:
                return ward

        except Exception:
            continue

    # ------------------------------------------------------------------
    # 2. Actual ward names
    # ------------------------------------------------------------------

    for ward in index.wards:

        ward_name = str(
            ward.get(
                "name",
                "",
            )
        ).replace(
            "-",
            " ",
        )

        parts = ward_name.split()

        for part in parts:

            p = (
                part
                .casefold()
                .strip()
            )

            if (
                len(p) <= 3
                or p in GENERIC_NAME_PARTS
            ):
                continue

            try:

                pattern = (
                    r"(?<![\w\u0900-\u097F])"
                    + re.escape(p)
                    + r"(?![\w\u0900-\u097F])"
                )

                if re.search(
                    pattern,
                    t,
                ):
                    return ward

            except Exception:
                continue

    return None


# ============================================================================
# STRICT POINT-IN-WARD + GEOCODING
# ============================================================================

def _ward_containing_point(
    lat: float,
    lon: float,
    index,
) -> dict | None:
    """
    The ward whose polygon actually contains the point, else None.

    Unlike WardIndex.ward_for_point this has NO nearest-centroid fallback,
    so a place outside every PMC ward is not forced into the closest one.
    """

    from shapely.geometry import Point

    pt = Point(
        lon,
        lat,
    )

    for ward in index.wards:

        if ward["geom"].contains(
            pt
        ):
            return ward

    return None


def _ward_from_places(
    names,
    index,
):
    """
    Geocode each place named in the post.

    Returns:
        (ward, lat, lon, place_name)

    for the first place that lies inside a PMC ward.
    """

    seen = set()

    for name in names:

        name = str(
            name or ""
        ).strip()

        if not name:
            continue

        key = name.casefold()

        if key in seen:
            continue

        seen.add(key)

        try:

            point = geocode_place(
                name
            )

            if not point:
                continue

            ward = _ward_containing_point(
                point[0],
                point[1],
                index,
            )

            if ward:

                return (
                    ward,
                    point[0],
                    point[1],
                    name,
                )

        except Exception:

            logger.exception(
                "Geocoding/ward check failed for %r",
                name,
            )

    return None


# ============================================================================
# DATE NORMALISATION
# ============================================================================

def _to_iso(
    value,
) -> str | None:
    """
    Convert common date formats to ISO-8601.

    Supports:
        RFC 822
        ISO
        ISO with Z
        ISO with +0000
    """

    if not value:
        return None

    value = str(
        value
    ).strip()

    if not value:
        return None

    # RSS / RFC 822
    try:

        dt = parsedate_to_datetime(
            value
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        ).isoformat()

    except Exception:
        pass

    # ISO
    try:

        normalised = (
            value
            .replace(
                "Z",
                "+00:00",
            )
            .replace(
                "+0000",
                "+00:00",
            )
        )

        dt = datetime.fromisoformat(
            normalised
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        ).isoformat()

    except Exception:
        return None


# ============================================================================
# DISPLAY-ONLY FALLBACK POINT
# ============================================================================

def _approx_point_in_ward(
    seed_text: str,
    ward: dict,
) -> tuple[float, float]:
    """
    Deterministic display-only point inside/around a ward.

    Retained for backwards compatibility.

    MUST NOT be used for citywide reports.
    """

    h = int(
        hashlib.md5(
            seed_text.encode(
                "utf-8",
                errors="ignore",
            )
        ).hexdigest(),
        16,
    )

    angle = (
        h % 360
    ) * (
        math.pi / 180
    )

    minx, miny, maxx, maxy = (
        ward["geom"].bounds
    )

    radius = max(
        maxx - minx,
        maxy - miny,
    ) * 0.2

    centroid = (
        ward["geom"].centroid
    )

    lon = (
        centroid.x
        + radius
        * math.cos(angle)
    )

    lat = (
        centroid.y
        + radius
        * math.sin(angle)
    )

    return lat, lon


# ============================================================================
# REPORT ID
# ============================================================================

def _report_id(
    post: dict,
) -> str:
    """
    Generate a stable report ID.

    URL is preferred for social posts because the same Instagram post can
    arrive through multiple hashtag searches.
    """

    url = str(
        post.get(
            "url"
        )
        or ""
    ).strip()

    if url:

        raw = "|".join(
            [
                str(
                    post.get(
                        "source"
                    )
                    or ""
                ),
                url,
            ]
        )

    else:

        raw = "|".join(
            [
                str(
                    post.get(
                        "source"
                    )
                    or ""
                ),
                str(
                    post.get(
                        "posted_at"
                    )
                    or ""
                ),
                str(
                    post.get(
                        "text"
                    )
                    or ""
                ),
            ]
        )

    return hashlib.sha1(
        raw.encode(
            "utf-8",
            errors="ignore",
        )
    ).hexdigest()[:20]


# ============================================================================
# DUPLICATE KEY
# ============================================================================

def _duplicate_key(
    post: dict,
) -> tuple:
    """
    Build a cross-source duplicate key.

    For Instagram, URL is the strongest identifier because the same post
    can be discovered through several hashtags and through reel search.

    For RSS/social feeds, normalized text remains the fallback.
    """

    url = str(
        post.get(
            "url"
        )
        or ""
    ).strip().lower()

    source = str(
        post.get(
            "source"
        )
        or ""
    ).strip().lower()

    if url:

        return (
            source,
            url,
        )

    text = re.sub(
        r"\W+",
        " ",
        str(
            post.get(
                "text",
                "",
            )
        ).casefold(),
    ).strip()

    return (
        source,
        text,
    )


# ============================================================================
# INGESTION
# ============================================================================

def run_ingestion(
    _sources=None,
) -> dict:
    """
    Run all configured ingestion sources.

    Behaviour:

    - Reports with coordinates are assigned by point-in-polygon.
    - Reports with a ward/location hint are assigned by that hint.
    - Reports mentioning a locality/ward are assigned by text.
    - PMC-related reports without a specific ward are retained as citywide.
    - Citywide reports have ward_id=None.
    """

    index = get_index()

    reports = []
    sources_used = []

    seen = set()

    # ------------------------------------------------------------------
    # SOURCES
    # ------------------------------------------------------------------

    sources = (
        _sources
        if _sources is not None
        else all_sources()
    )

    for source in sources:

        source_name = type(
            source
        ).__name__

        try:

            if (
                hasattr(
                    source,
                    "available",
                )
                and not source.available()
                and source_name
                != "MockSource"
            ):

                logger.info(
                    "%s unavailable - skipping",
                    source_name,
                )

                continue

            batch = source.fetch()

        except Exception:

            logger.exception(
                "Source %s failed, skipping",
                source_name,
            )

            continue

        if not batch:

            logger.info(
                "%s returned 0 posts",
                source_name,
            )

            continue

        sources_used.append(
            source_name
        )

        placed = 0
        citywide = 0
        dropped = 0

        dropped_examples = []

        # ------------------------------------------------------------------
        # POSTS
        # ------------------------------------------------------------------

        for post in batch:

            text = str(
                post.get(
                    "text"
                )
                or ""
            ).strip()

            if not text:
                continue

            # --------------------------------------------------------------
            # Duplicate detection
            # --------------------------------------------------------------

            key = _duplicate_key(
                post
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            # --------------------------------------------------------------
            # LOCATION
            # --------------------------------------------------------------

            try:

                analysis = classify_post(
                    text
                )

            except Exception:

                logger.exception(
                    "Classification failed; using fallback analysis"
                )

                analysis = {
                    "category": "other",
                    "category_label": (
                        "General PMC Update"
                    ),
                    "category_label_native": (
                        "सामान्य PMC अपडेट"
                    ),
                    "matched_terms": [],
                    "language": None,
                    "translated_text": None,
                    "places": [],
                    "location_hint": None,
                    "summary_en": None,
                    "lang": None,
                }

            ward = None

            has_real_coords = (
                post.get("lat")
                is not None
                and post.get("lon")
                is not None
            )

            hint = str(
                post.get(
                    "place_hint"
                )
                or ""
            ).strip()

            location_source = None

            # --------------------------------------------------------------
            # 1. Explicit operator hint
            # --------------------------------------------------------------

            if hint:

                ward = _match_ward_by_name(
                    hint,
                    index,
                )

                if ward:
                    location_source = (
                        "ward_hint"
                    )

            # --------------------------------------------------------------
            # 2. Real coordinates
            # --------------------------------------------------------------

            if (
                ward is None
                and has_real_coords
            ):

                try:

                    ward = index.ward_for_point(
                        float(
                            post["lat"]
                        ),
                        float(
                            post["lon"]
                        ),
                    )

                    if ward:

                        location_source = (
                            "post_coordinates"
                        )

                except Exception:

                    logger.exception(
                        "Point-in-polygon failed for post"
                    )

            # --------------------------------------------------------------
            # 3. Places named in post
            # --------------------------------------------------------------

            geo_point = None
            geo_place = None

            if ward is None:

                names = list(
                    analysis.get(
                        "places"
                    )
                    or []
                )

                if analysis.get(
                    "location_hint"
                ):

                    names.append(
                        analysis[
                            "location_hint"
                        ]
                    )

                found = _ward_from_places(
                    names,
                    index,
                )

                if found:

                    (
                        ward,
                        g_lat,
                        g_lon,
                        geo_place,
                    ) = found

                    geo_point = (
                        g_lat,
                        g_lon,
                    )

                    location_source = (
                        "geocoded_place"
                    )

            # --------------------------------------------------------------
            # 4. Caption / title / hashtags
            # --------------------------------------------------------------

            if ward is None:

                ward = _match_ward_by_name(
                    text,
                    index,
                )

                if ward:

                    location_source = (
                        "caption_or_hashtag"
                    )

            # --------------------------------------------------------------
            # 5. Sarvam AI location interpretation
            # --------------------------------------------------------------

            if ward is None:

                for field, label in (
                    (
                        "location_hint",
                        "sarvam_location",
                    ),
                    (
                        "translated_text",
                        "sarvam_translation",
                    ),
                    (
                        "summary_en",
                        "sarvam_translation",
                    ),
                ):

                    candidate = analysis.get(
                        field
                    )

                    if not candidate:
                        continue

                    ward = _match_ward_by_name(
                        str(
                            candidate
                        ),
                        index,
                    )

                    if ward:

                        location_source = label

                        break

            # --------------------------------------------------------------
            # CITYWIDE HANDLING
            # --------------------------------------------------------------

            is_citywide = (
                ward is None
            )

            if is_citywide:

                citywide += 1

                location_precision = (
                    "citywide"
                )

                lat = None
                lon = None

                ward_id = None
                ward_name = "PMC-wide"

                location_source = (
                    location_source
                    or "no_specific_ward"
                )

            else:

                placed += 1

                ward_id = ward[
                    "id"
                ]

                ward_name = ward[
                    "name"
                ]

                # ----------------------------------------------------------
                # Coordinates
                # ----------------------------------------------------------

                if geo_point is not None:

                    lat, lon = geo_point

                    location_precision = (
                        "exact"
                    )

                elif has_real_coords:

                    lat = float(
                        post["lat"]
                    )

                    lon = float(
                        post["lon"]
                    )

                    location_precision = (
                        "exact"
                    )

                else:

                    # Actual ward centroid is used only as the display
                    # location. It is not claimed to be the issue's
                    # exact location.

                    centroid = (
                        ward["geom"].centroid
                    )

                    lat = centroid.y
                    lon = centroid.x

                    location_precision = (
                        "ward"
                    )

            # --------------------------------------------------------------
            # REPORT
            # --------------------------------------------------------------

            report = {
                "id": _report_id(
                    post
                ),

                "source": post.get(
                    "source"
                ),

                "source_name": post.get(
                    "source_name"
                ),

                "text": text,

                "posted_at": post.get(
                    "posted_at"
                ),

                "created_at": _to_iso(
                    post.get(
                        "posted_at"
                    )
                    or post.get(
                        "created_at"
                    )
                ),

                # ----------------------------------------------------------
                # Classification
                # ----------------------------------------------------------

                "category": analysis.get(
                    "category",
                    "other",
                ),

                "category_label": analysis.get(
                    "category_label",
                    "General PMC Update",
                ),

                "category_label_native": (
                    analysis.get(
                        "category_label_native",
                        "सामान्य PMC अपडेट",
                    )
                ),

                "matched_terms": (
                    analysis.get(
                        "matched_terms",
                        [],
                    )
                ),

                "language": analysis.get(
                    "language"
                ),

                "translated_text": (
                    analysis.get(
                        "translated_text"
                    )
                ),

                "lang": analysis.get(
                    "lang"
                ),

                "summary_en": analysis.get(
                    "summary_en"
                ),

                "location_hint": (
                    analysis.get(
                        "location_hint"
                    )
                ),

                "places": (
                    analysis.get(
                        "places"
                    )
                    or []
                ),

                "geocoded_place": (
                    geo_place
                ),

                # ----------------------------------------------------------
                # Ward
                # ----------------------------------------------------------

                "ward_id": ward_id,

                "ward_name": ward_name,

                # ----------------------------------------------------------
                # Coordinates
                # ----------------------------------------------------------

                "lat": lat,

                "lon": lon,

                # ----------------------------------------------------------
                # Location metadata
                # ----------------------------------------------------------

                "location_precision": (
                    location_precision
                ),

                "location_source": (
                    location_source
                ),

                # ----------------------------------------------------------
                # Social/media
                # ----------------------------------------------------------

                "embed": bool(
                    post.get(
                        "embed"
                    )
                ),

                "url": post.get(
                    "url"
                ),

                "media_url": (
                    post.get(
                        "media_url"
                    )
                ),

                # ----------------------------------------------------------
                # Instagram metadata
                # ----------------------------------------------------------

                "instagram_author": (
                    post.get(
                        "instagram_author"
                    )
                ),

                "instagram_discovery": (
                    post.get(
                        "instagram_discovery"
                    )
                ),
            }

            reports.append(
                report
            )

        # ------------------------------------------------------------------
        # SOURCE SUMMARY
        # ------------------------------------------------------------------

        logger.warning(
            "%s: %d post(s) in, "
            "%d placed on a ward, "
            "%d citywide, "
            "%d dropped",
            source_name,
            len(batch),
            placed,
            citywide,
            dropped,
        )

        if dropped_examples:

            logger.warning(
                "%s dropped examples: %s",
                source_name,
                " | ".join(
                    dropped_examples
                ),
            )

    # =========================================================================
    # DEMO FALLBACK
    # =========================================================================

    if (
        not reports
        and _sources is None
        and os.environ.get(
            "CIVIC_MOCK_FALLBACK",
            "0",
        )
        != "0"
    ):

        logger.warning(
            "No live PMC reports found. "
            "Showing demo posts instead."
        )

        demo = run_ingestion(
            [
                MockSource()
            ]
        )

        demo[
            "demo_fallback"
        ] = True

        return demo

    # =========================================================================
    # PAYLOAD
    # =========================================================================

    payload = {
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),

        "sources_used": (
            sources_used
        ),

        "report_count": len(
            reports
        ),

        "citywide_count": sum(
            1
            for report in reports
            if report.get(
                "location_precision"
            )
            == "citywide"
        ),

        "reports": reports,
    }

    # =========================================================================
    # SAVE
    # =========================================================================

    LIVE_REPORTS_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    LIVE_REPORTS_PATH.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    logger.info(
        "Civic ingestion complete: "
        "%d reports, %d citywide",
        len(
            reports
        ),
        payload[
            "citywide_count"
        ],
    )

    return payload


# ============================================================================
# LOAD REPORTS
# ============================================================================

def load_reports() -> dict:
    """
    Load cached reports.

    If cache doesn't exist, perform ingestion.
    """

    if not LIVE_REPORTS_PATH.exists():

        return run_ingestion()

    try:

        return json.loads(
            LIVE_REPORTS_PATH.read_text(
                encoding="utf-8"
            )
        )

    except Exception:

        logger.exception(
            "Could not read live_reports.json; "
            "running fresh ingestion."
        )

        return run_ingestion()

