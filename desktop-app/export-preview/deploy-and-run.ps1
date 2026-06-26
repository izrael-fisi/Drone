$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step($Message) {
  Write-Host ""
  Write-Host "[Drone Vision Nav] $Message" -ForegroundColor Cyan
}

Write-Step "Preparing Windows UI preview."

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
  Write-Step "Node.js was not found."
  $winget = Get-Command winget -ErrorAction SilentlyContinue
  if ($winget) {
    Write-Step "Installing Node.js LTS with winget."
    winget install --id OpenJS.NodeJS.LTS -e --accept-package-agreements --accept-source-agreements
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    $node = Get-Command node -ErrorAction SilentlyContinue
  }
}

if (-not $node) {
  Write-Host ""
  Write-Host "Node.js is required to run this preview." -ForegroundColor Yellow
  Write-Host "Install Node.js LTS, then re-run this script:"
  Write-Host "  https://nodejs.org/"
  Write-Host ""
  Read-Host "Press Enter to exit"
  exit 1
}

if (-not $env:PORT) {
  $env:PORT = "1420"
}

$url = "http://127.0.0.1:$env:PORT/"
Write-Step "Starting preview server at $url"
Start-Process $url
Write-Host ""
Write-Host "Optional demo data page:" -ForegroundColor DarkGray
Write-Host "  http://127.0.0.1:$env:PORT/seed-demo-state.html" -ForegroundColor DarkGray
Write-Host ""
Write-Host "Press Ctrl+C in this window to stop the server."
Write-Host ""
node server.mjs
