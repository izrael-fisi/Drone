[CmdletBinding()]
param(
  [ValidateSet("dev", "build", "preview", "shell", "install", "down", "clean", "logs")]
  [string]$Action = "dev"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$ComposeFile = Join-Path $RepoRoot "docker\desktop\docker-compose.yml"

function Invoke-DesktopCompose {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
  )

  & docker compose -f $ComposeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
  }
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Host "Install Docker Desktop with: winget install Docker.DockerDesktop"
  Write-Error "Docker was not found. Install Docker Desktop, start it, then rerun this script."
}

switch ($Action) {
  "dev" {
    Write-Host "Starting Drone desktop web runtime at http://localhost:5173"
    Invoke-DesktopCompose up --build desktop-web
  }
  "build" {
    Write-Host "Building Drone desktop frontend inside Docker"
    Invoke-DesktopCompose run --rm desktop-web npm run build
  }
  "preview" {
    Write-Host "Building and previewing Drone desktop frontend at http://localhost:4173"
    Invoke-DesktopCompose run --rm desktop-web npm run build
    Invoke-DesktopCompose run --rm --service-ports desktop-web npm run preview -- --host 0.0.0.0 --port 4173
  }
  "shell" {
    Invoke-DesktopCompose run --rm desktop-web sh
  }
  "install" {
    Write-Host "Refreshing container node_modules from package-lock.json"
    Invoke-DesktopCompose run --rm desktop-web npm ci
  }
  "down" {
    Invoke-DesktopCompose down
  }
  "clean" {
    Write-Host "Removing desktop Docker containers and node_modules volume"
    Invoke-DesktopCompose down -v --remove-orphans
  }
  "logs" {
    Invoke-DesktopCompose logs -f desktop-web
  }
}
