[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8001,
    [bool]$CleanNext = $true,
    [int]$TimeoutSeconds = 40
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$FrontendDir = Join-Path $Root "frontend"
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$PowerShellExe = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[dev] $Message"
}

function Quote-Ps {
    param([string]$Value)
    return "'" + ($Value -replace "'", "''") + "'"
}

function Stop-PortListener {
    param([int]$Port)

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique

    foreach ($processId in $listeners) {
        if (-not $processId) {
            continue
        }
        try {
            $process = Get-Process -Id $processId -ErrorAction Stop
            Write-Step "Stopping port $Port listener: $($process.ProcessName) ($processId)"
            Stop-Process -Id $processId -Force -ErrorAction Stop
        } catch {
            Write-Step "Port $Port listener $processId was already gone"
        }
    }
}

function Wait-Http {
    param(
        [string]$Name,
        [string]$Url,
        [int]$Seconds
    )

    $deadline = (Get-Date).AddSeconds($Seconds)
    $lastError = $null

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
                Write-Step "$Name is ready: HTTP $($response.StatusCode)"
                return $response
            }
            $lastError = "HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 1
    }

    throw "$Name did not become ready at $Url. Last error: $lastError"
}

function Start-DevShell {
    param(
        [string]$Name,
        [string]$Command
    )

    Write-Step "Starting $Name"
    Start-Process `
        -FilePath $PowerShellExe `
        -ArgumentList @("-NoProfile", "-NoExit", "-Command", $Command) `
        -WindowStyle Hidden | Out-Null
}

function Clear-NextCache {
    $nextDir = Join-Path $FrontendDir ".next"
    $resolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    $resolvedNext = Resolve-Path -LiteralPath $nextDir -ErrorAction SilentlyContinue
    if ($resolvedNext -and $resolvedNext.Path.StartsWith($resolvedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-Step "Removing stale Next.js cache: $($resolvedNext.Path)"
        Remove-Item -LiteralPath $resolvedNext.Path -Recurse -Force
    }
}

function Start-Frontend {
    param([string]$Command)
    Stop-PortListener -Port $FrontendPort
    Start-Sleep -Seconds 1
    Start-DevShell -Name "frontend" -Command $Command
}

function Test-FrontendCss {
    param([string]$Html)

    $matches = [regex]::Matches($Html, 'href="([^"]*\.css[^"]*)"')
    if ($matches.Count -eq 0) {
        throw "Frontend HTML did not include a CSS link. The page may render without styles."
    }

    $cssHref = $matches[0].Groups[1].Value
    if ($cssHref.StartsWith("/")) {
        $cssUrl = "$frontendUrl$cssHref"
    } else {
        $cssUrl = $cssHref
    }
    Wait-Http -Name "frontend css" -Url $cssUrl -Seconds 10 | Out-Null
}

if (-not (Test-Path -LiteralPath $BackendPython)) {
    throw "Backend virtualenv python not found: $BackendPython"
}

if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "package.json"))) {
    throw "Frontend package.json not found: $FrontendDir"
}

Write-Step "Workspace: $Root"
Stop-PortListener -Port $FrontendPort
Stop-PortListener -Port $BackendPort
Start-Sleep -Seconds 1

if ($CleanNext) {
    Clear-NextCache
}

$backendLog = Join-Path $Root "backend-dev.log"
$frontendLog = Join-Path $Root "frontend-dev.log"

$backendCommand = @(
    "Set-Location $(Quote-Ps $Root)",
    ".\backend\venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host $HostName --port $BackendPort 2>&1 | Tee-Object -FilePath $(Quote-Ps $backendLog)"
) -join "; "

$frontendCommand = @(
    "Set-Location $(Quote-Ps $FrontendDir)",
    "npm.cmd run dev -- -H $HostName -p $FrontendPort 2>&1 | Tee-Object -FilePath $(Quote-Ps $frontendLog)"
) -join "; "

Start-DevShell -Name "backend" -Command $backendCommand
Start-Frontend -Command $frontendCommand

$backendUrl = "http://${HostName}:${BackendPort}"
$frontendUrl = "http://${HostName}:${FrontendPort}"

Wait-Http -Name "backend" -Url "$backendUrl/api/health" -Seconds $TimeoutSeconds | Out-Null
$frontendResponse = Wait-Http -Name "frontend" -Url $frontendUrl -Seconds $TimeoutSeconds

try {
    Test-FrontendCss -Html $frontendResponse.Content
} catch {
    Write-Step "Frontend CSS check failed: $($_.Exception.Message)"
    Write-Step "Restarting frontend once with a clean Next.js cache"
    Clear-NextCache
    Start-Frontend -Command $frontendCommand
    $frontendResponse = Wait-Http -Name "frontend" -Url $frontendUrl -Seconds $TimeoutSeconds
    Test-FrontendCss -Html $frontendResponse.Content
}

Write-Host ""
Write-Host "Ready:"
Write-Host "  Frontend: $frontendUrl"
Write-Host "  Backend:  $backendUrl"
Write-Host "  Logs:     $backendLog"
Write-Host "            $frontendLog"
Write-Host ""
Write-Host "Tip: run .\scripts\smoke-test.ps1 to verify the main flow."
