$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
  Write-Host ""
  Write-Host "[Drone Vision Nav] Node.js was not found." -ForegroundColor Yellow
  Write-Host "Install Node.js LTS from https://nodejs.org/ and run this script again."
  Write-Host ""
  Read-Host "Press Enter to exit"
  exit 1
}

if (-not $env:PORT) {
  $env:PORT = "1420"
}

$url = "http://127.0.0.1:$env:PORT/"
Write-Host ""
Write-Host "[Drone Vision Nav] Starting UI preview on $url"
Write-Host ""
Start-Process $url
node server.mjs
