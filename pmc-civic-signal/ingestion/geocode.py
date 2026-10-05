"""Turn a place name from a post ("Seven Loves Chowk", "सेवन लव्ह्ज चौक") into a lat/lon.

Lookup order:
  1. data/place_coords.json  - your own gazetteer: {"seven loves chowk": [18.50, 73.87], ...}
                               Always wins. Devanagari names work too. Add places here
                               whenever the online lookup gets one wrong.
  2. OpenStreetMap Nominatim - restricted to the Pune area. Free, no key, ~1 request/second.
  3. Nothing found -> None (the pipeline then falls back to name matching).

Answers are cached in data/geocode_cache.json so each place is looked up once.
Set CIVIC_GEOCODE=0 to turn the online lookup off. Never raises.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
GAZETTEER_PATH = BASE_DIR / "data" / "place_coords.json"
CACHE_PATH = BASE_DIR / "data" / "geocode_cache.json"

# Generous box around PMC limits (incl. merged villages). Results outside it are rejected.
LON_MIN, LON_MAX = 73.70, 74.05
LAT_MIN, LAT_MAX = 18.40, 18.66

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "PMC-Civic-Signal/1.0 (civic issue dashboard)"

_last_call = 0.0
_cache = None
_gazetteer = None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\u0900-\u097F]+", "", (s or "").casefold())


def _in_pune(lat, lon) -> bool:
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _load_gazetteer() -> dict:
    global _gazetteer
    if _gazetteer is None:
        _gazetteer = {}
        try:
            raw = json.loads(GAZETTEER_PATH.read_text(encoding="utf-8"))
            for name, pt in raw.items():
                if isinstance(pt, (list, tuple)) and len(pt) == 2 and not str(name).startswith("_"):
                    _gazetteer[_norm(name)] = (float(pt[0]), float(pt[1]))
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Could not read %s", GAZETTEER_PATH, exc_info=True)
    return _gazetteer


def _load_cache() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            _cache = {}
    return _cache


def _save_cache():
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        logger.warning("Could not write geocode cache", exc_info=True)


def _nominatim(name: str):
    """One Nominatim query. Returns (lat, lon), None (looked up, not found) or 'error'."""
    global _last_call
    try:
        import requests
        wait = 1.1 - (time.time() - _last_call)   # Nominatim usage policy: max 1 request/second
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        r = requests.get(
            NOMINATIM_URL,
            params={
                "q": f"{name}, Pune, Maharashtra",
                "format": "jsonv2", "limit": 3, "countrycodes": "in",
                "viewbox": f"{LON_MIN},{LAT_MAX},{LON_MAX},{LAT_MIN}", "bounded": 1,
            },
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=10,
        )
        r.raise_for_status()
        for hit in r.json():
            lat, lon = float(hit["lat"]), float(hit["lon"])
            if _in_pune(lat, lon):
                return (lat, lon)
        return None
    except Exception:
        logger.warning("Geocoding request failed for %r", name, exc_info=True)
        return "error"


def geocode_place(name: str):
    """Return (lat, lon) for a place name, or None."""
    name = (name or "").strip()
    key = _norm(name)
    if not key or len(key) < 3:
        return None

    gz = _load_gazetteer()
    if key in gz:
        return gz[key]

    cache = _load_cache()
    if key in cache:
        v = cache[key]
        return tuple(v) if v else None

    if os.environ.get("CIVIC_GEOCODE", "1") == "0":
        return None

    res = _nominatim(name)
    if res == "error":          # network problem: don't cache, retry on the next ingest
        return None
    cache[key] = list(res) if res else None
    _save_cache()
    return res
