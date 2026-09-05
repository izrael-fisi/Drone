<#
Copies explicit Pi training-capture sessions to a Windows research workstation
and validates every copied session before any labels or training are produced.

This is a data-transfer utility only. It never opens PX4 MAVLink, starts a
camera, changes Pi files, trains a model, or publishes a vision estimate.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PiHost,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$PiUser,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string[]]$RemoteCaptureSessionDir,
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$DestinationRoot,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    throw "scp was not found. Install Windows OpenSSH Client or Git for Windows, then retry."
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Research Python was not found: $PythonPath. Run .\scripts\research\setup_windows_cuda_research.ps1 first."
}

$Destination = [System.IO.Path]::GetFullPath($DestinationRoot)
if (Test-Path -LiteralPath $Destination) {
    if ((Get-ChildItem -Force -LiteralPath $Destination).Count -gt 0) {
        throw "DestinationRoot must be new or empty: $Destination"
    }
} else {
    New-Item -ItemType Directory -Path $Destination | Out-Null
}

$RemoteSessions = @()
foreach ($RemoteSession in $RemoteCaptureSessionDir) {
    $Normalized = $RemoteSession.Trim().TrimEnd("/")
    if (-not $Normalized -or -not $Normalized.Contains("/")) {
        throw "Each RemoteCaptureSessionDir must be a full Pi session directory, such as /home/user/drone-data/captures/route-a."
    }
    $Leaf = ($Normalized -split "/")[-1]
    if (-not $Leaf -or $Leaf -in $RemoteSessions.leaf) {
        throw "Remote capture sessions must have unique directory names."
    }
    $RemoteSessions += [PSCustomObject]@{ path = $Normalized; leaf = $Leaf }
}

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Validation failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

Write-Host "Copying immutable Pi capture sessions" -ForegroundColor Cyan
$CopiedSessions = @()
foreach ($Session in $RemoteSessions) {
    $Source = "$PiUser@$PiHost`:$($Session.path)"
    Write-Host "Copying $Source" -ForegroundColor Cyan
    & scp -r -- $Source $Destination
    if ($LASTEXITCODE -ne 0) {
        throw "scp failed while copying $Source"
    }
    $LocalSession = Join-Path $Destination $Session.leaf
    if (-not (Test-Path -LiteralPath (Join-Path $LocalSession "manifest.json") -PathType Leaf)) {
        throw "Copied session does not contain manifest.json: $LocalSession"
    }
    Write-Host "Validating copied session $($Session.leaf)" -ForegroundColor Cyan
    Invoke-ResearchPython -c @'
from pathlib import Path
import json
import sys
from vision_nav.training_capture import validate_training_session

result = validate_training_session(
    Path(sys.argv[1]),
    require_attitude=True,
    require_sensor_timestamps=True,
    require_offline_gnss_reference=True,
    require_mavlink_timesync=True,
)
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result["status"] == "valid" else 1)
'@ $LocalSession
    $CopiedSessions += $LocalSession
}

$Manifest = [ordered]@{
    schema_version = "vision_nav_windows_pi_capture_import_v1"
    status = "validated"
    source = [ordered]@{ pi_host = $PiHost; pi_user = $PiUser; remote_capture_sessions = @($RemoteSessions.path) }
    destination_root = $Destination
    capture_sessions = $CopiedSessions
    policy = "offline_training_only"
    px4_external_vision = "disabled"
    next_action = "Use capture_sessions as CaptureSessionDir inputs to prepare_first_temporal_windows.ps1."
}
$ManifestPath = Join-Path $Destination "import_manifest.json"
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Write-Host "Pi capture import and validation completed." -ForegroundColor Green
Write-Host "Manifest: $ManifestPath"
Write-Host "Next: .\scripts\research\prepare_first_temporal_windows.ps1 -CaptureSessionDir $($CopiedSessions -join ',') -ReferenceMap <reference_map.json> -OutputRoot <fresh-output-root> -DatasetReleaseId <id> -DatasetReleaseVersion <version>"
