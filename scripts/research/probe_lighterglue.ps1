<#
Records and validates a research-only official XFeat LighterGlue probe.
It never creates a ModelRelease, transfers to a Pi, or publishes PX4 output.

Example:
  .\scripts\research\probe_lighterglue.ps1 `
    -Checkout D:\DroneData\vendor\xfeat `
    -Weights D:\DroneData\weights\xfeat-lighterglue.pt `
    -OutputDir D:\DroneData\research-runs\lighterglue-probe-v1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Checkout,
    [Parameter(Mandatory = $true)][string]$Weights,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [int]$FeatureCount = 1024,
    [int]$ImageWidth = 640,
    [int]$ImageHeight = 384,
    [double]$MinimumConfidence = 0.2,
    [ValidateSet("cpu", "cuda")][string]$Device = "cuda",
    [string]$PythonCommand = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if ([string]::IsNullOrWhiteSpace($PythonCommand)) {
    $Candidate = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
    $PythonCommand = if (Test-Path $Candidate) { $Candidate } else { "py" }
}

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    if ($PythonCommand -eq "py") {
        & py -3.12 @Arguments
    } else {
        & $PythonCommand @Arguments
    }
    if ($LASTEXITCODE -ne 0) { throw "Research Python command failed with exit code $LASTEXITCODE." }
}

if (-not (Test-Path $Checkout -PathType Container)) { throw "Checkout does not exist: $Checkout" }
if (-not (Test-Path $Weights -PathType Leaf)) { throw "Weights do not exist: $Weights" }
if (Test-Path $OutputDir) {
    $Existing = Get-ChildItem -Force $OutputDir -ErrorAction SilentlyContinue
    if ($Existing) { throw "OutputDir must be empty: $OutputDir" }
}

Invoke-ResearchPython -m vision_nav.neural.lighterglue_probe `
    --checkout $Checkout --weights $Weights --output-dir $OutputDir `
    --upstream-lock research\upstreams.lock.json --feature-count $FeatureCount `
    --image-width $ImageWidth --image-height $ImageHeight `
    --minimum-confidence $MinimumConfidence --device $Device
Invoke-ResearchPython -m vision_nav.neural.lighterglue_probe --probe-dir $OutputDir

Write-Host "LighterGlue probe is valid and remains research-only." -ForegroundColor Green
