<#
Exports the exact pinned XFeat backbone to an auditable research-only ONNX
artifact. It does not make a ModelRelease, copy to a Pi, open MAVLink, or
enable PX4 external vision.

Example:
  .\scripts\research\export_xfeat_dense_onnx.ps1 `
    -Checkout D:\DroneData\vendor\xfeat `
    -Weights D:\DroneData\weights\xfeat.pt `
    -OutputDir D:\DroneData\research-runs\xfeat-dense-v1
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
    [int]$ImageHeight = 480
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if ($ImageWidth % 32 -ne 0 -or $ImageHeight % 32 -ne 0) {
    throw "ImageWidth and ImageHeight must both be divisible by 32 for pinned XFeat."
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
        throw "OutputDir must be new or empty so the XFeat export has immutable provenance: $ResolvedOutput"
    }
}

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Research command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

Write-Host "Exporting pinned XFeat dense backbone" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.xfeat_export `
    --checkout $ResolvedCheckout --weights $ResolvedWeights --output-dir $ResolvedOutput `
    --upstream-lock research\upstreams.lock.json --image-width $ImageWidth --image-height $ImageHeight --device $Device
Invoke-ResearchPython -c "from vision_nav.neural.xfeat_export import validate_main; validate_main()" `
    --export-dir $ResolvedOutput --output (Join-Path $ResolvedOutput "export_validation.json")
Invoke-ResearchPython -c "from vision_nav.neural.xfeat_export import parity_main; parity_main()" `
    --export-dir $ResolvedOutput --output (Join-Path $ResolvedOutput "onnx_parity.json")

Write-Host "XFeat dense export and ONNX parity completed." -ForegroundColor Green
Write-Host "This is not a ModelRelease and must not be copied to the Pi as a flight artifact." -ForegroundColor Yellow
