<#
Prepares an isolated Windows RTX research environment. It does not contact the
Pi, change PX4, or create a field model release.

Run from PowerShell:
  .\scripts\research\setup_windows_cuda_research.ps1

If PyTorch changes its CUDA wheel index, provide the current official index:
  .\scripts\research\setup_windows_cuda_research.ps1 -TorchIndexUrl https://download.pytorch.org/whl/cu124
#>
[CmdletBinding()]
param(
    [string]$PythonCommand = "py",
    [string]$PythonVersion = "-3.12",
    [string]$TorchIndexUrl = "https://download.pytorch.org/whl/cu124",
    [switch]$SkipTorchInstall
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

Write-Host "Drone Vision Nav CUDA research setup" -ForegroundColor Cyan
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
} else {
    Write-Warning "nvidia-smi was not found. Install/update the NVIDIA driver before a CUDA training run."
}

& $PythonCommand $PythonVersion -m venv .venv-research
$Python = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
& $Python -m pip install --upgrade pip setuptools wheel

if (-not $SkipTorchInstall) {
    Write-Host "Installing PyTorch from $TorchIndexUrl" -ForegroundColor Cyan
    & $Python -m pip install torch torchvision --index-url $TorchIndexUrl
}

& $Python -m pip install -e ".[research,neural-runtime,map-production]"
& $Python -m pip install -r research\requirements-research.txt

Write-Host "Running the read-only CUDA research readiness gate" -ForegroundColor Cyan
& $Python -m vision_nav.neural.research_environment `
    --require-cuda `
    --require-map-production `
    --require-onnx `
    --require-lighterglue `
    --output research\environment_report.json

Write-Host "Ready for a bounded temporal teacher smoke run." -ForegroundColor Green
Write-Host "The first training command remains research-only and cannot publish PX4 external vision."
