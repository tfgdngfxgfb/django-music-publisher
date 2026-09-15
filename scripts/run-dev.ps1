[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$AdminUsername = "admin",
    [string]$AdminPassword = "",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvPip = Join-Path $ProjectRoot ".venv\Scripts\pip.exe"
$Requirements = Join-Path $ProjectRoot "requirements-dev.txt"
$RequirementsStamp = Join-Path $ProjectRoot ".venv\.requirements-dev.stamp"

Set-Location $ProjectRoot

if (-not (Test-Path $VenvPython)) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    if (-not $Python) {
        throw "Den nye Python-launcheren ble ikke funnet. Installer eller endre den manuelt før du fortsetter."
    }
    & $Python.Source -V:3.14 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Kunne ikke opprette .venv med Python 3.14." }
}

$VenvVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $VenvVersion.Trim() -ne "3.14") {
    throw ".venv må bruke Python 3.14. Miljøet ble ikke endret automatisk."
}

$RequiredStamp = (Get-Item $Requirements).LastWriteTimeUtc.Ticks.ToString()
$InstalledStamp = if (Test-Path $RequirementsStamp) {
    (Get-Content $RequirementsStamp -Raw).Trim()
} else {
    ""
}

if ($RequiredStamp -ne $InstalledStamp) {
    Write-Host "Installing/updating Python dependencies..."
    & $VenvPip install --disable-pip-version-check -r $Requirements
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    Set-Content -Path $RequirementsStamp -Value $RequiredStamp -Encoding ascii
}

if (-not (Test-Path (Join-Path $ProjectRoot ".env"))) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") (Join-Path $ProjectRoot ".env")
    Write-Host "Created .env from .env.example."
}

Write-Host "Applying database migrations..."
& $VenvPython manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

$env:P7_DEV_ADMIN_USERNAME = $AdminUsername
if ($AdminPassword) { $env:P7_DEV_ADMIN_PASSWORD = $AdminPassword }
& $VenvPython manage.py ensure_dev_admin
Remove-Item Env:P7_DEV_ADMIN_USERNAME -ErrorAction SilentlyContinue
Remove-Item Env:P7_DEV_ADMIN_PASSWORD -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the local administrator." }

& $VenvPython manage.py check
if ($LASTEXITCODE -ne 0) { throw "Django system check failed." }

Write-Host ""
Write-Host "P7 Arkiv og rettigheter er klar."
Write-Host "Adresse:      http://${HostAddress}:$Port/"
Write-Host "Administrasjon: http://${HostAddress}:$Port/admin/"
Write-Host "Brukernavn:   $AdminUsername"

if (-not $CheckOnly) {
    Write-Host "Trykk Ctrl+C for å stoppe programmet."
    & $VenvPython manage.py runserver "${HostAddress}:$Port"
}
