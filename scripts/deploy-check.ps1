[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$SkipBackendTests,
    [switch]$SkipComposeConfig
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvPath = Join-Path $Root ".env"
$FrontendDir = Join-Path $Root "frontend"
$BackendDir = Join-Path $Root "backend"
$BackendPython = Join-Path $BackendDir "venv\Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[deploy-check] $Message"
}

function Assert-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command not found: $Name"
    }
}

Write-Step "Checking environment file"
if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Missing .env. Copy .env.production.example to .env and replace every replace-me value."
}

$envContent = Get-Content -LiteralPath $EnvPath -Raw
if ($envContent -match "replace-me") {
    throw ".env still contains replace-me placeholders."
}

$requiredKeys = @(
    "OPENAI_API_KEY",
    "NEO4J_PASSWORD",
    "MINIO_ACCESS_KEY",
    "MINIO_SECRET_KEY",
    "NEXT_PUBLIC_API_URL",
    "CORS_ORIGINS"
)

foreach ($key in $requiredKeys) {
    if ($envContent -notmatch "(?m)^$key=.+") {
        throw ".env is missing required key: $key"
    }
}

if (-not $SkipBackendTests) {
    Write-Step "Running backend tests"
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend virtualenv python not found: $BackendPython"
    }
    Push-Location $BackendDir
    try {
        & $BackendPython -m unittest discover -s tests
    } finally {
        Pop-Location
    }
}

if (-not $SkipFrontendBuild) {
    Write-Step "Building frontend"
    Assert-Command "npm.cmd"
    Push-Location $FrontendDir
    try {
        & npm.cmd run build
    } finally {
        Pop-Location
    }
}

if (-not $SkipComposeConfig) {
    Write-Step "Validating docker compose config"
    Assert-Command "docker"
    Push-Location $Root
    try {
        & docker compose config --quiet
    } finally {
        Pop-Location
    }
}

Write-Host ""
Write-Host "Deploy check passed."
