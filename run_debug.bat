@echo off
cd /d "%~dp0"
python main.py
if errorlevel 1 (
    echo.
    echo Не удалось запустить. Проверьте data\logs\app.log
    pause
)