@echo off
REM ============================================================
REM  PMC Civic Signal: Windows setup script
REM  Double-click this file to set everything up. Run ONCE.
REM  After that, use run_server.bat to start it.
REM ============================================================

cd /d "%~dp0"

echo.
echo === Checking Python ===
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Python was not found on this computer, or it is not on PATH.
    echo Install it from https://www.python.org/downloads/ and check
    echo "Add Python to PATH" during install.
    echo.
    pause
    exit /b 1
)
python --version

echo.
echo === Creating virtual environment (venv folder) ===
if exist venv (
    echo A venv folder already exists here - skipping creation.
) else (
    python -m venv venv
)

call venv\Scripts\activate.bat

echo.
echo === Installing dependencies ===
python -m pip install --upgrade pip
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Something went wrong during installation. Scroll up for details.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Setup complete! Double-click run_server.bat to start the app.
echo  (Optional) Set ANTHROPIC_API_KEY as a system env var first if
echo  you want AI category refinement and ward briefs enabled.
echo ============================================================
pause
