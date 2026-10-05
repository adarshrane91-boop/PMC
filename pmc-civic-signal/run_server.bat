@echo off
setlocal
cd /d "%~dp0"

echo.
echo ============================================================
echo PMC Civic Signal - Backend
echo ============================================================
echo.

REM Public PMC Facebook/Instagram post mode needs NO Meta access token.
REM Add known public post URLs and captions through POST /api/posts.
REM Optional API keys can be supplied as Windows environment variables:
REM   SARVAM_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY
REM   SOCIALCRAWL_API_KEY (optional Instagram public discovery)

set "CIVIC_PMC_ONLY=1"
set "CIVIC_PLACE_UNMATCHED=1"
set "CIVIC_DISABLE_SOCIAL=1"
set "CIVIC_MOCK_FALLBACK=0"
set "CIVIC_NEWS_RSS_FEEDS=https://news.google.com/rss/search?q=Pune+Municipal+Corporation+when:7d&hl=en-IN&gl=IN&ceid=IN:en"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.11+ and run: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

python -c "import fastapi, uvicorn; print('Python dependencies OK')"
if errorlevel 1 (
    echo ERROR: Required Python packages are missing.
    echo Run: python -m pip install -r requirements.txt
    pause
    exit /b 1
)

echo.
echo Starting PMC Civic Signal at http://localhost:8000
 echo Sources: public PMC post links + Google News RSS + optional public Instagram discovery
 echo Press Ctrl+C to stop the server.
echo.

uvicorn main:app --reload --host 0.0.0.0 --port 8000

endlocal
