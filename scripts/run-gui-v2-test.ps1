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
$LaunchParameters = @{
    HostAddress = $HostAddress
    Port = $Port
    AdminUsername = $AdminUsername
    Reset = $Reset
    CheckOnly = $CheckOnly
}
if ($AdminPassword) { $LaunchParameters.AdminPassword = $AdminPassword }

# GUI v2 og Workbench bruker samme separate testdatabase. Dette navnet
# beholdes som et praktisk alias, men skriptet laster ikke egne demodata.
& (Join-Path $PSScriptRoot "run-empty-test.ps1") @LaunchParameters
if ($LASTEXITCODE -ne 0) { throw "Kunne ikke starte GUI v2 med testdatabasen." }
