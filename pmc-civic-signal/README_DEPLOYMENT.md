# PMC Civic Signal — Deployment Package

This package is the cleaned PMC Civic Signal ward-news/social-intelligence application.

## Frontend
The frontend intentionally remains **one file**:

`static/index.html`

HTML, CSS and JavaScript are kept together to match the existing project structure.

## Scope kept
- PMC ward boundaries (`data/pmc_wards.geojson`)
- Ward-wise civic reports/news
- Public PMC Facebook/Instagram post links without Meta access tokens
- Google News/local RSS ingestion
- Optional public Instagram discovery through SocialCrawl
- Optional X source when `X_BEARER_TOKEN` is configured
- Marathi/Hindi/English classification and Sarvam enrichment
- Optional Gemini/Claude ward action briefs
- Original source links and map popups

## Removed
- Drone/orthomosaic/TIFF processing
- Tree/canopy detection
- Tree detection previews
- Infrastructure GIS layer processing and UI
- Bundled Python virtual environment
- Hard-coded API credentials

## Windows
```bat
python -m pip install -r requirements.txt
run_server.bat
```

## Linux/cloud
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Set secrets through the hosting provider's environment-variable settings.

## Public Facebook/Instagram posts
The token-free design does not claim that a public profile URL grants permission to enumerate every post. Operators add known public post URLs and captions through `/api/posts`; the pipeline then classifies and maps them.
