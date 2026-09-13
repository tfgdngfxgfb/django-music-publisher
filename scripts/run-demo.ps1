[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$AdminUsername = "admin",
    [string]$AdminPassword = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DemoRoot = Join-Path $ProjectRoot ".local\demo-nas"
$DemoDatabase = (Join-Path $ProjectRoot ".local\p7-demo.sqlite3").Replace("\", "/")

$env:DATABASE_URL = "sqlite:///$DemoDatabase"
$env:P7_NAS_ROOT = $DemoRoot
$env:P7_MUSIC_ROOT = $DemoRoot
$env:DEBUG = "true"

& (Join-Path $PSScriptRoot "run-dev.ps1") -HostAddress $HostAddress -Port $Port -AdminUsername $AdminUsername -AdminPassword $AdminPassword -CheckOnly
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke klargjøre demoapplikasjonen." }

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
& $VenvPython (Join-Path $ProjectRoot "manage.py") load_demo_data --files-root $DemoRoot
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke laste demo-data." }

Write-Host ""
Write-Host "P7-demoen er klar med en separat database og faste testfiler."
Write-Host "Adresse: http://${HostAddress}:$Port/"
Write-Host "Brukernavn: $AdminUsername"
Write-Host "Trykk Ctrl+C for å stoppe programmet."
& $VenvPython (Join-Path $ProjectRoot "manage.py") runserver "${HostAddress}:$Port"
