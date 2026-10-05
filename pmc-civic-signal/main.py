"""PMC Civic Signal - civic news and social intelligence backend.

FastAPI backend for PMC ward boundaries, civic-news ingestion, public
Facebook/Instagram post links, RSS news, multilingual classification and
optional ward action briefs.

Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
import json
import os
from collections import Counter, defaultdict
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from brief_generator import generate_brief
from ingestion.pipeline import load_reports, run_ingestion
from ingestion.sources import PublicSocialSource, RssNewsSource
from wards_gis import get_index

app = FastAPI(title="PMC Civic Signal", version="2.0")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
def refresh_civic_feed_on_startup():
    try:
        run_ingestion()
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            "Initial civic feed ingestion failed; existing cache will be served."
        )


@app.get("/")
def root():
    return FileResponse("static/index.html")


@app.get("/api/wards")
def get_wards():
    index = get_index()
    data = load_reports()
    counts_by_ward = defaultdict(Counter)
    for r in data["reports"]:
        counts_by_ward[r.get("ward_id")][r.get("category", "other")] += 1

    features = index.as_feature_collection()
    for feat in features["features"]:
        wid = feat["properties"]["ward_id"]
        feat["properties"]["report_counts"] = dict(counts_by_ward.get(wid, {}))
        feat["properties"]["total_reports"] = sum(counts_by_ward.get(wid, {}).values())

    return {
        "generated_at": data["generated_at"],
        "sources_used": data["sources_used"],
        "wards": features,
    }


@app.get("/api/public-sources")
def get_public_sources():
    return {
        "facebook": {
            "name": "PMC Pune", "handle": "PMCPune",
            "url": "https://www.facebook.com/PMCPune",
            "mode": "public-post-links-no-token",
        },
        "instagram": {
            "name": "PMC Pune", "handle": "pmccarepune",
            "url": "https://www.instagram.com/pmccarepune/",
            "mode": "public-post-links-no-token",
        },
        "news": {
            "name": "Google News RSS",
            "url": "https://news.google.com/",
            "mode": "rss",
        },
    }


@app.get("/api/source-status")
def get_source_status():
    social = PublicSocialSource()
    rss = RssNewsSource()
    try:
        social_raw = json.loads(social.path.read_text(encoding="utf-8")) if social.path.exists() else {}
        social_posts = social_raw if isinstance(social_raw, list) else social_raw.get("posts", [])
    except Exception:
        social_posts = []
    return {
        "facebook": {
            "official": True,
            "public_post_links_configured": sum(1 for x in social_posts if isinstance(x, dict) and str(x.get("source", "")).lower() == "facebook"),
            "account_url": "https://www.facebook.com/PMCPune",
        },
        "instagram": {
            "official": True,
            "public_post_links_configured": sum(1 for x in social_posts if isinstance(x, dict) and str(x.get("source", "")).lower() == "instagram"),
            "account_url": "https://www.instagram.com/pmccarepune/",
        },
        "google_news_rss": {"enabled": rss.available(), "feed_count": len(rss.feeds())},
        "sarvam_ai": {"configured": bool(os.environ.get("SARVAM_API_KEY"))},
    }


@app.get("/api/reports")
def get_all_reports():
    data = load_reports()
    return {
        "generated_at": data["generated_at"],
        "sources_used": data["sources_used"],
        "reports": data["reports"],
    }


@app.get("/api/wards/{ward_id}")
def get_ward_reports(ward_id: int):
    index = get_index()
    ward = next((w for w in index.wards if w["id"] == ward_id), None)
    if ward is None:
        raise HTTPException(404, "Unknown ward_id")
    data = load_reports()
    reports = [r for r in data["reports"] if r.get("ward_id") == ward_id]
    return {"ward_id": ward_id, "ward_name": ward["name"], "reports": reports}


@app.post("/api/wards/{ward_id}/brief")
def post_ward_brief(ward_id: int):
    index = get_index()
    ward = next((w for w in index.wards if w["id"] == ward_id), None)
    if ward is None:
        raise HTTPException(404, "Unknown ward_id")
    data = load_reports()
    reports = [r for r in data["reports"] if r.get("ward_id") == ward_id]
    brief = generate_brief(ward["name"], reports)
    if brief is None:
        return {
            "available": False,
            "reason": "Set GEMINI_API_KEY or ANTHROPIC_API_KEY (and the matching package) to enable AI briefs, or there are no reports yet for this ward.",
        }
    return {"available": True, "brief": brief.__dict__}


@app.post("/api/ingest")
def post_ingest():
    return run_ingestion()


class PublicPostIn(BaseModel):
    url: str
    text: str
    posted_at: Optional[str] = None
    ward: Optional[str] = None


@app.post("/api/posts")
def add_public_posts(posts: List[PublicPostIn]):
    """Add known public PMC Facebook/Instagram post URLs and their captions.

    This intentionally does not use Meta access tokens or scrape account feeds.
    A public post URL plus its caption is supplied by an operator, then the
    normal classification and ward-assignment pipeline processes it.
    """
    path = PublicSocialSource.DEFAULT_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        raw = {}
    if isinstance(raw, list):
        raw = {"posts": raw}
    existing = {p.get("url"): p for p in raw.get("posts", []) if isinstance(p, dict) and p.get("url")}
    rejected = []
    for p in posts:
        src = PublicSocialSource._source_from_url(p.url)
        if src is None or not p.text.strip():
            rejected.append(p.url)
            continue
        entry = {"source": src, "url": p.url.strip(), "text": p.text.strip()}
        if p.posted_at:
            entry["posted_at"] = p.posted_at
        if p.ward:
            entry["ward"] = p.ward
        existing[entry["url"]] = entry
    raw["posts"] = list(existing.values())
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    result = run_ingestion()
    urls = {p.url.strip() for p in posts}
    placed = [r for r in result["reports"] if r.get("url") in urls]
    return {
        "saved": len(posts) - len(rejected),
        "rejected_not_facebook_or_instagram_url": rejected,
        "placed_on_map": len(placed),
        "not_placed": sorted(urls - {r.get("url") for r in placed} - set(rejected)),
        "reports": placed,
    }
