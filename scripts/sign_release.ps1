#Requires -Version 5.1
<#
.SYNOPSIS
  Sign TelegramHashtagDownloader.exe with an Authenticode certificate.

.DESCRIPTION
  Requires a code-signing certificate (.pfx) from a trusted CA.
  Self-signed certs do NOT remove SmartScreen warnings for public users.

  Example purchase: SSL.com, Sectigo, DigiCert (Standard or EV code signing).

.PARAMETER PfxPath
  Path to .pfx file.

.PARAMETER PfxPassword
  Certificate password (or use env SIGN_PFX_PASSWORD).

.PARAMETER TimestampUrl
  RFC 3161 timestamp server (recommended so signature stays valid after cert expiry).
#>
param(
    [string]$PfxPath = $env:SIGN_PFX_PATH,
    [string]$PfxPassword = $env:SIGN_PFX_PASSWORD,
    [string]$TimestampUrl = "http://timestamp.digicert.com"
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$exe = Join-Path $root "dist\TelegramHashtagDownloader\TelegramHashtagDownloader.exe"

if (-not (Test-Path $exe)) {
    throw "Build the app first: .\scripts\build_release.ps1"
}
if (-not $PfxPath -or -not (Test-Path $PfxPath)) {
    throw "Set SIGN_PFX_PATH to your .pfx code-signing certificate."
}

$signtool = Get-Command signtool.exe -ErrorAction SilentlyContinue
if (-not $signtool) {
    throw "signtool.exe not found. Install Windows SDK (Signing Tools for Windows)."
}

$passArg = @()
if ($PfxPassword) {
    $passArg = @("/p", $PfxPassword)
}

& signtool.exe sign /fd SHA256 /tr $TimestampUrl /td SHA256 /f $PfxPath @passArg $exe
& signtool.exe verify /pa $exe
Write-Host "Signed: $exe"
