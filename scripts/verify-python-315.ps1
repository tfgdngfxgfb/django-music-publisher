[CmdletBinding()]
param(
    [switch]$Recreate,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvironmentRoot = Join-Path $ProjectRoot ".local\venvs\python315"
$VenvPython = Join-Path $EnvironmentRoot "Scripts\python.exe"
$VenvPip = Join-Path $EnvironmentRoot "Scripts\pip.exe"
$Requirements = Join-Path $ProjectRoot "requirements-dev.txt"

Set-Location $ProjectRoot

$Launcher = Get-Command py -ErrorAction SilentlyContinue
if (-not $Launcher) {
    throw "Den nye Python-launcheren ble ikke funnet. Installer eller endre den manuelt før du fortsetter."
}

$RuntimeVersion = & $Launcher.Source -V:3.15 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $RuntimeVersion.Trim() -ne "3.15") {
    throw "Python 3.15 finnes ikke i den nye launcheren. Ingen runtime ble installert eller endret."
}

if ($Recreate -and (Test-Path -LiteralPath $EnvironmentRoot)) {
    $LocalRoot = [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot ".local"))
    $ResolvedEnvironment = [System.IO.Path]::GetFullPath($EnvironmentRoot)
    if (-not $ResolvedEnvironment.StartsWith($LocalRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Avbrøt fordi preview-miljøet ligger utenfor .local."
    }
    Remove-Item -LiteralPath $ResolvedEnvironment -Recurse -Force
}

if (-not (Test-Path -LiteralPath $VenvPython)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $EnvironmentRoot) -Force | Out-Null
    & $Launcher.Source -V:3.15 -m venv $EnvironmentRoot
    if ($LASTEXITCODE -ne 0) { throw "Kunne ikke opprette det isolerte Python 3.15-miljøet." }
}

$VenvVersion = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $VenvVersion.Trim() -ne "3.15") {
    throw "Det eksisterende preview-miljøet bruker ikke Python 3.15. Kjør med -Recreate dersom det skal erstattes."
}

& $VenvPip install --disable-pip-version-check -r $Requirements
if ($LASTEXITCODE -ne 0) { throw "Dependency-installasjon feilet på Python 3.15." }

& $VenvPip check
if ($LASTEXITCODE -ne 0) { throw "pip check feilet på Python 3.15." }

& $VenvPython manage.py check
if ($LASTEXITCODE -ne 0) { throw "Django system check feilet på Python 3.15." }

& $VenvPython manage.py makemigrations --check --dry-run
if ($LASTEXITCODE -ne 0) { throw "Migrasjonskontrollen feilet på Python 3.15." }

if (-not $SkipTests) {
    & $VenvPython manage.py test
    if ($LASTEXITCODE -ne 0) { throw "Testsuiten feilet på Python 3.15." }
}

Write-Host "Python 3.15 preview-verifikasjon fullført."
