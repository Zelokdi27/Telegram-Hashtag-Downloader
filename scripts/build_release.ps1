#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Generating version_info.txt..."
python scripts/generate_version_info.py

Write-Host "Installing dependencies..."
python -m pip install -r requirements.txt -r requirements-build.txt

Write-Host "Building Windows release folder..."
python -m PyInstaller packaging/telegram-hashtag-downloader.spec --noconfirm --distpath dist --workpath build

$out = Join-Path (Get-Location) "dist\TelegramHashtagDownloader"
if (-not (Test-Path $out)) {
    throw "Build output not found: $out"
}

Write-Host ""
Write-Host "Done. Portable app folder:"
Write-Host "  $out"
Write-Host ""
Write-Host "Zip this folder for release. On first run it creates .env and data/ next to the exe."
