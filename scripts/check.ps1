[CmdletBinding()]
param(
    [switch]$SkipFrontendBuild,
    [switch]$SkipDockerComposeConfig,
    [switch]$SkipGoldenEval
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path

function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Command
    )
    Write-Host ""
    Write-Host "[check] $Name"
    & $Command
}

Invoke-Step "Backend unit tests" {
    & "$Root\scripts\test-backend.ps1"
}

Invoke-Step "Frontend typecheck" {
    Push-Location (Join-Path $Root "frontend")
    try {
        & npm.cmd run typecheck
    } finally {
        Pop-Location
    }
}

Invoke-Step "Frontend lint" {
    Push-Location (Join-Path $Root "frontend")
    try {
        & npm.cmd run lint
    } finally {
        Pop-Location
    }
}

if (-not $SkipFrontendBuild) {
    Invoke-Step "Frontend production build" {
        Push-Location (Join-Path $Root "frontend")
        try {
            & npm.cmd run build
        } finally {
            Pop-Location
        }
    }
}

if (-not $SkipDockerComposeConfig) {
    Invoke-Step "Docker Compose config" {
        Push-Location $Root
        try {
            & docker compose config --quiet
        } finally {
            Pop-Location
        }
    }
}

if (-not $SkipGoldenEval) {
    Invoke-Step "Golden Eval quality gate" {
        & "$Root\scripts\rag-golden-eval.cmd"
    }
}

Write-Host ""
Write-Host "All checks passed."
