[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$AdminUsername = "admin",
    [string]$AdminPassword = "123",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements-dev.txt"
$RequirementsStamp = Join-Path $ProjectRoot ".venv\.requirements-dev.stamp"

Set-Location $ProjectRoot

if (-not (Test-Path $VenvPython)) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    if ($Python) {
        & py -3.13 -m venv .venv
    } else {
        $Python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $Python) {
            throw "Python 3.13 was not found. Install Python, then run this script again."
        }
        & python -m venv .venv
    }
}

$RequiredStamp = (Get-Item $Requirements).LastWriteTimeUtc.Ticks.ToString()
$InstalledStamp = if (Test-Path $RequirementsStamp) {
    (Get-Content $RequirementsStamp -Raw).Trim()
} else {
    ""
}

if ($RequiredStamp -ne $InstalledStamp) {
    Write-Host "Installing/updating Python dependencies..."
    & $VenvPython -m pip install --disable-pip-version-check -r $Requirements
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
$env:P7_DEV_ADMIN_PASSWORD = $AdminPassword
& $VenvPython manage.py ensure_dev_admin
Remove-Item Env:P7_DEV_ADMIN_USERNAME, Env:P7_DEV_ADMIN_PASSWORD
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the local administrator." }

& $VenvPython manage.py check
if ($LASTEXITCODE -ne 0) { throw "Django system check failed." }

Write-Host ""
Write-Host "P7 Rights is ready."
Write-Host "Address:  http://${HostAddress}:$Port/"
Write-Host "Admin:    http://${HostAddress}:$Port/admin/"
Write-Host "Username: $AdminUsername"
Write-Host "Password: $AdminPassword"

if (-not $CheckOnly) {
    Write-Host "Press Ctrl+C to stop the application."
    & $VenvPython manage.py runserver "${HostAddress}:$Port"
}
