[CmdletBinding()]
param(
    [string]$HostAddress = "127.0.0.1",
    [int]$Port = 8000,
    [string]$AdminUsername = "admin",
    [string]$AdminPassword = "",
    [switch]$Reset,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LocalRoot = Join-Path $ProjectRoot ".local"
$DatabasePath = Join-Path $LocalRoot "p7-empty-test.sqlite3"
$BackupRoot = Join-Path $LocalRoot "database-backups"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

New-Item -ItemType Directory -Path $LocalRoot -Force | Out-Null

if ($Reset -and (Test-Path -LiteralPath $DatabasePath)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackupPath = Join-Path $BackupRoot "p7-empty-test-$Timestamp.sqlite3"
    Move-Item -LiteralPath $DatabasePath -Destination $BackupPath
    Write-Host "Forrige testdatabase er arkivert som $BackupPath"
}

$DatabaseUrlPath = [System.IO.Path]::GetFullPath($DatabasePath).Replace("\", "/")
$env:DATABASE_URL = "sqlite:///$DatabaseUrlPath"
$env:GUI_V2_WRITES_ENABLED = "true"
$env:P7_ALLOW_FILE_WRITES = "false"
$env:DEBUG = "true"

& (Join-Path $PSScriptRoot "run-dev.ps1") `
    -HostAddress $HostAddress `
    -Port $Port `
    -AdminUsername $AdminUsername `
    -AdminPassword $AdminPassword `
    -CheckOnly
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke klargjøre den tomme testdatabasen." }

Write-Host ""
Write-Host "P7 Arkiv og rettigheter bruker den separate testdatabasen."
Write-Host "Database: $DatabasePath"
Write-Host "GUI v2 (primær): http://${HostAddress}:$Port/"
Write-Host "GUI v1 (eldre): http://${HostAddress}:$Port/arbeid/"
Write-Host "Brukernavn: $AdminUsername"
Write-Host "-Reset arkiverer databasen og bygger den tom igjen."

if (-not $CheckOnly) {
    Write-Host "Stopp programmet med Ctrl+C."
    & $VenvPython (Join-Path $ProjectRoot "manage.py") runserver "${HostAddress}:$Port"
}
