@echo off
cd /d "%~dp0"
title Hashtag Downloader - autotests

set "LOG_DIR=%~dp0data\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "tokens=1-3 delims=/:. " %%a in ("%date% %time%") do set "STAMP=%%c%%b%%a_%%d"
set "STAMP=%STAMP: =0%"
set "LOG_FILE=%LOG_DIR%\tests_%STAMP%.log"

echo.
echo  Telegram Hashtag Downloader - autotests
echo  Log: %LOG_FILE%
echo  ========================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ and add to PATH.
    goto end
)

python -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo Installing pytest...
    python -m pip install -r requirements-dev.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install pytest.
        goto end
    )
)

echo Running tests...
echo.
python -m pytest tests -v --tb=short --durations=10 > "%LOG_FILE%" 2>&1
set EXIT_CODE=%ERRORLEVEL%
type "%LOG_FILE%"

echo.
if %EXIT_CODE%==0 (
    echo [OK] All tests passed.
) else (
    echo [FAIL] Some tests failed. Exit code: %EXIT_CODE%
)
echo Log saved: %LOG_FILE%

:end
echo.
pause
if not defined EXIT_CODE set EXIT_CODE=1
exit /b %EXIT_CODE%