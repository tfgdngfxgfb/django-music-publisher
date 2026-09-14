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
$DatabasePath = Join-Path $LocalRoot "gui-v2-test.sqlite3"
$FileRoot = Join-Path $LocalRoot "gui-v2-files"
$BackupRoot = Join-Path $LocalRoot "database-backups"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Path $LocalRoot,$FileRoot -Force | Out-Null
if ($Reset -and (Test-Path -LiteralPath $DatabasePath)) {
    New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
    $BackupPath = Join-Path $BackupRoot ("gui-v2-test-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".sqlite3")
    Move-Item -LiteralPath $DatabasePath -Destination $BackupPath
    Write-Host "Forrige GUI-v2-testdatabase er arkivert som $BackupPath"
}
$DatabaseUrlPath = [System.IO.Path]::GetFullPath($DatabasePath).Replace("\", "/")
$env:DATABASE_URL = "sqlite:///$DatabaseUrlPath"
$env:P7_NAS_ROOT = $FileRoot
$env:P7_MUSIC_ROOT = $FileRoot
$env:GUI_V2_WRITES_ENABLED = "true"
$env:DEBUG = "true"
& (Join-Path $PSScriptRoot "run-dev.ps1") -HostAddress $HostAddress -Port $Port -AdminUsername $AdminUsername -AdminPassword $AdminPassword -CheckOnly
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke klargjøre GUI-v2-testdatabasen." }
& $VenvPython (Join-Path $ProjectRoot "manage.py") load_gui_v2_test_data
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke laste GUI-v2-testdata." }
Write-Host ""
Write-Host "GUI v2 er klar på http://${HostAddress}:$Port/v2/"
Write-Host "Database: $DatabasePath"
Write-Host "Filrot: $FileRoot (ingen lydfiler opprettes eller endres)"
if (-not $CheckOnly) {
    Write-Host "Trykk Ctrl+C for å stoppe programmet."
    & $VenvPython (Join-Path $ProjectRoot "manage.py") runserver "${HostAddress}:$Port"
}
