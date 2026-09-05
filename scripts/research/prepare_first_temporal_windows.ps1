<#
Prepares the first CUDA-ready temporal-window package from copied Pi sessions.

This script is research-only. It validates existing capture evidence, builds
offline-only labels from recorded GNSS, and creates map-supervised windows from
a model-independent ReferenceMapRelease. It never connects to the Pi or PX4.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string[]]$CaptureSessionDir,
    [Parameter(Mandatory = $true)]
    [string]$ReferenceMap,
    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,
    [Parameter(Mandatory = $true)]
    [string]$DatasetReleaseId,
    [Parameter(Mandatory = $true)]
    [string]$DatasetReleaseVersion,
    [string]$PythonPath = "",
    [ValidateRange(2, 1000000)]
    [int]$MinSamples = 100,
    [ValidateRange(2, 10000)]
    [int]$MinCaptureSessions = 2,
    [ValidateRange(0.01, 0.99)]
    [double]$ValidationFraction = 0.2,
    [ValidateRange(1, 2147483647)]
    [int]$SplitSeed = 20260830,
    [ValidateRange(64, 4096)]
    [int]$TileSizePx = 512,
    [ValidateRange(1, 4096)]
    [int]$TileStridePx = 512,
    [ValidateRange(2, 32)]
    [int]$TemporalWindowSize = 4,
    [ValidateRange(1, 32)]
    [int]$TemporalStride = 1,
    [ValidateRange(1, 10000)]
    [double]$MaxFrameGapMs = 1000
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Research Python was not found: $PythonPath. Run .\scripts\research\setup_windows_cuda_research.ps1 first."
}

$ReferenceMapPath = (Resolve-Path -LiteralPath $ReferenceMap).Path
if (-not (Test-Path -LiteralPath $ReferenceMapPath -PathType Leaf)) {
    throw "ReferenceMap must be a readable reference_map.json: $ReferenceMap"
}
$SessionPaths = @()
foreach ($Session in $CaptureSessionDir) {
    $Path = (Resolve-Path -LiteralPath $Session).Path
    if (-not (Test-Path -LiteralPath (Join-Path $Path "manifest.json") -PathType Leaf)) {
        throw "Each CaptureSessionDir must contain a capture manifest.json: $Path"
    }
    $SessionPaths += $Path
}
if (($SessionPaths | Select-Object -Unique).Count -ne $SessionPaths.Count) {
    throw "CaptureSessionDir cannot contain the same session directory more than once."
}

$RunRoot = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $RunRoot) {
    if ((Get-ChildItem -Force -LiteralPath $RunRoot).Count -gt 0) {
        throw "OutputRoot must be new or empty so preparation has one immutable lineage: $RunRoot"
    }
} else {
    New-Item -ItemType Directory -Path $RunRoot | Out-Null
}

$DatasetReleasePath = Join-Path $RunRoot "dataset_release.json"
$IndexDir = Join-Path $RunRoot "training-index"
$LabelsDir = Join-Path $RunRoot "training-labels"
$SplitsDir = Join-Path $RunRoot "training-splits"
$ExperimentDir = Join-Path $RunRoot "teacher-experiment"
$TileLayoutDir = Join-Path $RunRoot "reference-tile-layout"
$PairsDir = Join-Path $RunRoot "training-map-pairs"
$WindowsDir = Join-Path $RunRoot "temporal-windows"

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Research command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

$SourceRevision = (& git -C $RepoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $SourceRevision) {
    throw "Could not record the current Git source revision for DatasetRelease provenance."
}

Write-Host "Checking required CUDA, map-production, and ONNX tooling" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.research_environment `
    --require-cuda --require-map-production --require-onnx `
    --output (Join-Path $RunRoot "environment_report.json")

Write-Host "Creating immutable DatasetRelease from validated Pi captures" -ForegroundColor Cyan
$DatasetArgs = @(
    "-m", "vision_nav.neural.dataset_release", "--output", $DatasetReleasePath,
    "--release-id", $DatasetReleaseId, "--version", $DatasetReleaseVersion,
    "--source-revision", $SourceRevision,
    "--license-summary", "proprietary Holybro capture data; no redistribution"
)
foreach ($SessionPath in $SessionPaths) {
    $DatasetArgs += @("--session", $SessionPath)
}
Invoke-ResearchPython @DatasetArgs

Write-Host "Building synchronized frame, attitude, and offline-GNSS index" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_dataset `
    --dataset-release $DatasetReleasePath --output-dir $IndexDir

Write-Host "Gating capture quality and timing before label generation" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_readiness `
    --index-dir $IndexDir --min-samples $MinSamples --min-capture-sessions $MinCaptureSessions `
    --output (Join-Path $IndexDir "training_readiness.json")

Write-Host "Materializing offline-only ENU and heading labels" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_labels `
    --index-dir $IndexDir --output-dir $LabelsDir --reference-map $ReferenceMapPath `
    --min-gps-fix-type 3 --max-gps-age-ms 500 --max-gps-horizontal-accuracy-m 10

Write-Host "Creating capture-session-disjoint train and validation splits" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_splits `
    --index-dir $IndexDir --output-dir $SplitsDir `
    --validation-fraction $ValidationFraction --seed $SplitSeed

Write-Host "Joining labels to immutable split provenance" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_experiment `
    --split-dir $SplitsDir --labels-dir $LabelsDir --output-dir $ExperimentDir --require-heading

Write-Host "Creating teacher-only map tiles from the ReferenceMapRelease" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.map_tile_layout `
    --reference-map $ReferenceMapPath --output-dir $TileLayoutDir `
    --tile-size-px $TileSizePx --stride-px $TileStridePx --min-valid-fraction 0.98

$TileLayout = Join-Path $TileLayoutDir "tile_layout.json"
Write-Host "Building verified UAV-to-map training pairs" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.training_map_pairs `
    --teacher-experiment $ExperimentDir --reference-map $ReferenceMapPath `
    --tile-layout $TileLayout --output-dir $PairsDir `
    --negative-count 4 --min-negative-distance-m 75 --max-positive-center-distance-m 100

Write-Host "Validating camera/map pairs and creating temporal windows" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.teacher_dataset `
    --pair-dir $PairsDir --require-heading --require-pose-context `
    --output (Join-Path $PairsDir "teacher_dataset_validation.json")
Invoke-ResearchPython -m vision_nav.neural.temporal_training_windows `
    --pair-dir $PairsDir --output-dir $WindowsDir --window-size $TemporalWindowSize `
    --stride $TemporalStride --max-frame-gap-ms $MaxFrameGapMs
Invoke-ResearchPython -c "from vision_nav.neural.temporal_training_windows import validate_main; validate_main()" `
    --window-dir $WindowsDir --output (Join-Path $WindowsDir "validation.json")

$ManifestPath = Join-Path $RunRoot "preparation_manifest.json"
$Manifest = [ordered]@{
    schema_version = "vision_nav_first_temporal_preparation_v1"
    status = "completed"
    policy = "offline_training_only"
    publication_mode = "research_only"
    px4_external_vision = "disabled"
    reference_map = $ReferenceMapPath
    capture_sessions = $SessionPaths
    dataset_release = $DatasetReleasePath
    training_index = $IndexDir
    labels = $LabelsDir
    splits = $SplitsDir
    teacher_experiment = $ExperimentDir
    reference_tile_layout = $TileLayout
    map_pairs = $PairsDir
    temporal_windows = $WindowsDir
    next_action = "Use temporal_windows as input to run_first_temporal_smoke.ps1. This preparation does not create a ModelRelease or Pi artifact."
}
$Manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Write-Host "Temporal training package prepared." -ForegroundColor Green
Write-Host "Windows: $WindowsDir"
Write-Host "Next: .\scripts\research\run_first_temporal_smoke.ps1 -TemporalWindowsDir `"$WindowsDir`" -OutputRoot <fresh-output-root>"
