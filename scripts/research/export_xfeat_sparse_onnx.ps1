<#
Exports fixed-cardinality local XFeat features to a research-only ONNX artifact.
The result must pass parity and an upstream-image comparison before it can be
bound to a ModelRelease. This script never copies to the Pi or publishes PX4
external vision.

Example:
  .\scripts\research\export_xfeat_sparse_onnx.ps1 `
    -Checkout D:\DroneData\vendor\xfeat `
    -Weights D:\DroneData\weights\xfeat.pt `
    -OutputDir D:\DroneData\research-runs\xfeat-sparse-v1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Checkout,

    [Parameter(Mandatory = $true)]
    [string]$Weights,

    [Parameter(Mandatory = $true)]
    [string]$OutputDir,

    [string]$PythonPath = "",
    [ValidateSet("cpu", "cuda")]
    [string]$Device = "cuda",
    [ValidateRange(32, 8192)]
    [int]$ImageWidth = 640,
    [ValidateRange(32, 8192)]
    [int]$ImageHeight = 480,
    [ValidateRange(1, 1000000)]
    [int]$MaximumFeatureCount = 2048,
    [ValidateRange(0.000001, 0.999999)]
    [double]$DetectionThreshold = 0.05
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if ($ImageWidth % 32 -ne 0 -or $ImageHeight % 32 -ne 0) {
    throw "ImageWidth and ImageHeight must both be divisible by 32 for pinned XFeat."
}
if ($MaximumFeatureCount -gt ($ImageWidth * $ImageHeight)) {
    throw "MaximumFeatureCount cannot exceed ImageWidth * ImageHeight."
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Research Python was not found: $PythonPath. Run .\scripts\research\setup_windows_cuda_research.ps1 first."
}

$ResolvedCheckout = (Resolve-Path -LiteralPath $Checkout).Path
$ResolvedWeights = (Resolve-Path -LiteralPath $Weights).Path
$ResolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
if (Test-Path -LiteralPath $ResolvedOutput) {
    $entries = Get-ChildItem -Force -LiteralPath $ResolvedOutput
    if ($entries.Count -gt 0) {
        throw "OutputDir must be new or empty so the XFeat sparse export has immutable provenance: $ResolvedOutput"
    }
}

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Research command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

Write-Host "Exporting fixed-cardinality XFeat local features" -ForegroundColor Cyan
Invoke-ResearchPython -c "from vision_nav.neural.xfeat_export import sparse_main; sparse_main()" `
    --checkout $ResolvedCheckout --weights $ResolvedWeights --output-dir $ResolvedOutput `
    --upstream-lock research\upstreams.lock.json --image-width $ImageWidth --image-height $ImageHeight `
    --maximum-feature-count $MaximumFeatureCount --detection-threshold $DetectionThreshold --device $Device
Invoke-ResearchPython -c "from vision_nav.neural.xfeat_export import sparse_validate_main; sparse_validate_main()" `
    --export-dir $ResolvedOutput --output (Join-Path $ResolvedOutput "export_validation.json")
Invoke-ResearchPython -c "from vision_nav.neural.xfeat_export import sparse_parity_main; sparse_parity_main()" `
    --export-dir $ResolvedOutput --output (Join-Path $ResolvedOutput "onnx_parity.json")

Write-Host "XFeat sparse export and ONNX parity completed." -ForegroundColor Green
Write-Host "It remains research-only until comparison against pinned upstream XFeat and held-out field data passes." -ForegroundColor Yellow
