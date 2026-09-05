<#
Runs a bounded, research-only learned-navigation smoke workflow on Windows.

This transforms verified temporal windows into a teacher checkpoint, immutable
teacher targets, a compact Pi temporal student, and a standalone ONNX parity
report. It never copies files to a Raspberry Pi, creates a ModelRelease, opens
MAVLink, or enables PX4 external vision.

Example:
  .\scripts\research\run_first_temporal_smoke.ps1 `
    -TemporalWindowsDir D:\DroneData\temporal-windows\first-site-v1 `
    -OutputRoot D:\DroneData\research-runs\first-site-smoke-v1
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$TemporalWindowsDir,

    [Parameter(Mandatory = $true)]
    [string]$OutputRoot,

    [string]$PythonPath = "",
    [ValidateSet("cuda", "cpu")]
    [string]$Device = "cuda",
    [ValidateRange(1, 1000)]
    [int]$TeacherEpochs = 1,
    [ValidateRange(1, 100000)]
    [int]$MaxTrainBatches = 10,
    [ValidateRange(1, 100000)]
    [int]$MaxValidationBatches = 10,
    [ValidateRange(64, 4096)]
    [int]$ImageWidth = 320,
    [ValidateRange(64, 4096)]
    [int]$ImageHeight = 320,
    [ValidateRange(16, 2048)]
    [int]$TeacherFeatureDim = 128,
    [ValidateRange(8, 1024)]
    [int]$StudentFeatureDim = 64,
    [ValidateRange(1, 2147483647)]
    [int]$Seed = 7
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
if ($TeacherFeatureDim % 4 -ne 0) {
    throw "TeacherFeatureDim must be divisible by the fixed four temporal-attention heads."
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $RepoRoot ".venv-research\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Research Python was not found: $PythonPath. Run .\scripts\research\setup_windows_cuda_research.ps1 first."
}

$WindowsDir = (Resolve-Path -LiteralPath $TemporalWindowsDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $WindowsDir "manifest.json") -PathType Leaf)) {
    throw "TemporalWindowsDir must contain manifest.json: $WindowsDir"
}

$RunRoot = [System.IO.Path]::GetFullPath($OutputRoot)
if (Test-Path -LiteralPath $RunRoot) {
    $existing = Get-ChildItem -Force -LiteralPath $RunRoot
    if ($existing.Count -gt 0) {
        throw "OutputRoot must be new or empty so one run has one immutable lineage: $RunRoot"
    }
} else {
    New-Item -ItemType Directory -Path $RunRoot | Out-Null
}

$TeacherDir = Join-Path $RunRoot "teacher"
$TargetsDir = Join-Path $RunRoot "teacher-targets"
$StudentDir = Join-Path $RunRoot "pi-student"
$ExportDir = Join-Path $RunRoot "pi-student-onnx"
$EvaluationPath = Join-Path $TeacherDir "heldout_evaluation.json"
$ExportValidationPath = Join-Path $ExportDir "export_validation.json"
$ParityPath = Join-Path $ExportDir "onnx_parity.json"

function Invoke-ResearchPython {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $PythonPath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Research command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

Write-Host "Checking CUDA, map-production, and ONNX tooling" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.research_environment `
    --require-cuda --require-map-production --require-onnx `
    --output (Join-Path $RunRoot "environment_report.json")

Write-Host "Validating temporal windows" -ForegroundColor Cyan
Invoke-ResearchPython -c "from vision_nav.neural.temporal_training_windows import validate_main; validate_main()" `
    --window-dir $WindowsDir `
    --output (Join-Path $RunRoot "temporal_windows_validation.json")

Write-Host "Training bounded temporal teacher smoke run" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.temporal_teacher_baseline `
    --window-dir $WindowsDir --output-dir $TeacherDir --device $Device `
    --epochs $TeacherEpochs --batch-size 1 --learning-rate 0.0001 --seed $Seed `
    --image-width $ImageWidth --image-height $ImageHeight `
    --feature-dim $TeacherFeatureDim --temporal-layers 2 --temporal-heads 4 `
    --max-train-batches $MaxTrainBatches --max-validation-batches $MaxValidationBatches

$TeacherCheckpoint = Join-Path $TeacherDir "temporal_teacher_baseline.pt"
Write-Host "Evaluating teacher checkpoint on held-out temporal windows" -ForegroundColor Cyan
Invoke-ResearchPython -c "from vision_nav.neural.temporal_teacher_baseline import evaluate_main; evaluate_main()" `
    --checkpoint $TeacherCheckpoint --window-dir $WindowsDir --device $Device `
    --max-validation-batches $MaxValidationBatches --output $EvaluationPath

Write-Host "Materializing immutable teacher targets" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.teacher_targets `
    --checkpoint $TeacherCheckpoint --window-dir $WindowsDir --output-dir $TargetsDir `
    --device $Device

Write-Host "Training compact Pi temporal student" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.pi_temporal_student `
    --window-dir $WindowsDir --target-dir $TargetsDir --output-dir $StudentDir `
    --device $Device --epochs $TeacherEpochs --learning-rate 0.0001 --seed $Seed `
    --image-width $ImageWidth --image-height $ImageHeight `
    --feature-dim $StudentFeatureDim --teacher-feature-dim $TeacherFeatureDim `
    --recurrent-layers 1 --max-train-batches $MaxTrainBatches `
    --max-validation-batches $MaxValidationBatches

$StudentCheckpoint = Join-Path $StudentDir "pi_temporal_student.pt"
Write-Host "Exporting and validating the Pi temporal student" -ForegroundColor Cyan
Invoke-ResearchPython -m vision_nav.neural.pi_temporal_student_export `
    --checkpoint $StudentCheckpoint --window-dir $WindowsDir --target-dir $TargetsDir `
    --output-dir $ExportDir --device $Device --opset-version 17
Invoke-ResearchPython -c "from vision_nav.neural.pi_temporal_student_export import validate_main; validate_main()" `
    --export-dir $ExportDir --output $ExportValidationPath
Invoke-ResearchPython -c "from vision_nav.neural.pi_temporal_student_export import parity_main; parity_main()" `
    --export-dir $ExportDir --output $ParityPath

$RunManifest = [ordered]@{
    schema_version = "vision_nav_first_temporal_smoke_run_v1"
    status = "completed"
    publication_mode = "research_only"
    px4_external_vision = "disabled"
    temporal_windows_dir = $WindowsDir
    temporal_windows_manifest_sha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $WindowsDir "manifest.json")).Hash.ToLowerInvariant()
    teacher_checkpoint = $TeacherCheckpoint
    teacher_evaluation = $EvaluationPath
    teacher_targets_dir = $TargetsDir
    student_checkpoint = $StudentCheckpoint
    student_export_dir = $ExportDir
    export_validation = $ExportValidationPath
    onnx_parity = $ParityPath
    parameters = [ordered]@{
        device = $Device; teacher_epochs = $TeacherEpochs; max_train_batches = $MaxTrainBatches
        max_validation_batches = $MaxValidationBatches; image_width = $ImageWidth; image_height = $ImageHeight
        teacher_feature_dim = $TeacherFeatureDim; student_feature_dim = $StudentFeatureDim; seed = $Seed
    }
    next_action = "Review teacher evaluation and ONNX parity. This smoke run is not a ModelRelease and must not be copied to Pi or published to PX4."
}
$ManifestPath = Join-Path $RunRoot "run_manifest.json"
$RunManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $ManifestPath -Encoding utf8

Write-Host "Research-only temporal smoke run completed." -ForegroundColor Green
Write-Host "Manifest: $ManifestPath"
Write-Host "No Pi transfer, ModelRelease creation, MAVLink command, or PX4 publication occurred."
