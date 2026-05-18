[CmdletBinding()]
param(
    [string]$EnvPath = ".env",
    [switch]$RequireEnv,
    [switch]$Production,
    [switch]$CheckServices,
    [switch]$SkipDocker
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$EnvFullPath = if ([System.IO.Path]::IsPathRooted($EnvPath)) {
    $EnvPath
} else {
    Join-Path $Root $EnvPath
}

$Failures = New-Object System.Collections.Generic.List[string]
$Warnings = New-Object System.Collections.Generic.List[string]

function Write-Step {
    param([string]$Message)
    Write-Host "[env-check] $Message"
}

function Add-Failure {
    param([string]$Message)
    $Failures.Add($Message) | Out-Null
    Write-Host "[fail] $Message" -ForegroundColor Red
}

function Add-Warning {
    param([string]$Message)
    $Warnings.Add($Message) | Out-Null
    Write-Host "[warn] $Message" -ForegroundColor Yellow
}

function Add-Ok {
    param([string]$Message)
    Write-Host "[ok] $Message" -ForegroundColor Green
}

function Test-Command {
    param([string]$Name)
    if (Get-Command $Name -ErrorAction SilentlyContinue) {
        Add-Ok "command available: $Name"
        return $true
    }
    Add-Failure "required command not found: $Name"
    return $false
}

function Read-DotEnv {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $map
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -ne 2) {
            continue
        }
        $map[$parts[0].Trim()] = $parts[1].Trim().Trim('"').Trim("'")
    }
    return $map
}

function Test-Http {
    param(
        [string]$Name,
        [string]$Url
    )
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
            Add-Ok "$Name returned HTTP $($response.StatusCode)"
            return $response
        }
        Add-Failure "$Name returned HTTP $($response.StatusCode): $Url"
    } catch {
        Add-Failure "$Name is not reachable at $Url. $($_.Exception.Message)"
    }
    return $null
}

Write-Step "Workspace: $Root"

Write-Step "Checking required local tools"
Test-Command "npm.cmd" | Out-Null
if (-not $SkipDocker) {
    if (Test-Command "docker") {
        try {
            & docker compose version | Out-Null
            Add-Ok "docker compose plugin available"
        } catch {
            Add-Failure "docker compose plugin is not available. $($_.Exception.Message)"
        }
    }
}

$backendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
if (Test-Path -LiteralPath $backendPython) {
    Add-Ok "backend virtualenv found"
} else {
    Add-Failure "backend virtualenv missing: $backendPython"
}

if (Test-Path -LiteralPath (Join-Path $Root "frontend\node_modules")) {
    Add-Ok "frontend node_modules found"
} else {
    Add-Warning "frontend node_modules missing. Run: cd frontend; npm.cmd install"
}

Write-Step "Checking environment file"
$envMap = Read-DotEnv -Path $EnvFullPath
if ($envMap.Count -eq 0) {
    $message = "No .env found at $EnvFullPath. Copy .env.example to .env for Docker or production-like runs."
    if ($RequireEnv -or $Production) {
        Add-Failure $message
    } else {
        Add-Warning $message
    }
} else {
    Add-Ok "environment file found: $EnvFullPath"
}

$requiredKeys = @(
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "EMBEDDING_MODEL",
    "LLM_BASE_URL",
    "NEXT_PUBLIC_API_URL",
    "CORS_ORIGINS",
    "MAX_UPLOAD_BYTES"
)

if ($Production) {
    $requiredKeys += @(
        "NEO4J_PASSWORD",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY"
    )
}

foreach ($key in ($requiredKeys | Select-Object -Unique)) {
    if ($envMap.Count -eq 0) {
        break
    }
    if (-not $envMap.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($envMap[$key])) {
        $message = ".env is missing key: $key"
        if ($RequireEnv -or $Production) {
            Add-Failure $message
        } else {
            Add-Warning $message
        }
        continue
    }
    if ($envMap[$key] -match "replace-me") {
        $message = ".env key still contains placeholder: $key"
        if ($Production) {
            Add-Failure $message
        } else {
            Add-Warning $message
        }
    }
}

if ($envMap.ContainsKey("DEBUG") -and $Production -and $envMap["DEBUG"].ToLowerInvariant() -ne "false") {
    Add-Failure "DEBUG must be false for production-like deployments."
}

if ($envMap.ContainsKey("CORS_ORIGINS") -and $Production -and $envMap["CORS_ORIGINS"] -match "127\.0\.0\.1|localhost") {
    Add-Warning "CORS_ORIGINS still points at localhost. Set it to the public frontend origin before public deployment."
}

Write-Step "Checking common ports"
foreach ($port in @(3000, 8000, 8001)) {
    $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($listeners) {
        $owners = ($listeners | Select-Object -ExpandProperty OwningProcess -Unique) -join ","
        Add-Warning "port $port is already in use by process id(s): $owners"
    } else {
        Add-Ok "port $port is available"
    }
}

if ($CheckServices) {
    Write-Step "Checking running services"
    Test-Http -Name "backend health" -Url "http://127.0.0.1:8001/api/health" | Out-Null
    $frontend = Test-Http -Name "frontend" -Url "http://127.0.0.1:3000"
    if ($frontend) {
        $match = [regex]::Match($frontend.Content, 'href="([^"]*\.css[^"]*)"')
        if ($match.Success) {
            $href = $match.Groups[1].Value
            $cssUrl = if ($href.StartsWith("/")) { "http://127.0.0.1:3000$href" } else { $href }
            Test-Http -Name "frontend css" -Url $cssUrl | Out-Null
        } else {
            Add-Failure "frontend HTML did not include a CSS file link."
        }
    }
}

Write-Host ""
Write-Host "Environment check summary:"
Write-Host "  Failures: $($Failures.Count)"
Write-Host "  Warnings: $($Warnings.Count)"

if ($Failures.Count -gt 0) {
    exit 1
}

Write-Host "Environment check passed."
