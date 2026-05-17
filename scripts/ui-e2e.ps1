[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$FrontendPort = 3100,
    [int]$BackendPort = 8101,
    [int]$TimeoutSeconds = 60,
    [switch]$KeepServices
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$FrontendDir = Join-Path $Root "frontend"
$FrontendUrl = "http://${HostName}:${FrontendPort}"
$BackendUrl = "http://${HostName}:${BackendPort}"

function Write-Step {
    param([string]$Message)
    Write-Host "[ui-e2e] $Message"
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

try {
    Write-Step "Starting isolated app services through API E2E smoke"
    & (Join-Path $PSScriptRoot "e2e-smoke.ps1") `
        -HostName $HostName `
        -FrontendPort $FrontendPort `
        -BackendPort $BackendPort `
        -TimeoutSeconds $TimeoutSeconds `
        -KeepServices

    if ($LASTEXITCODE -ne 0) {
        throw "API E2E smoke failed before UI E2E could run"
    }

    Write-Step "Running Playwright UI flow"
    Push-Location $FrontendDir
    try {
        $env:UI_E2E_BASE_URL = $FrontendUrl
        $env:UI_E2E_API_URL = $BackendUrl
        & npx.cmd playwright test
        if ($LASTEXITCODE -ne 0) {
            throw "Playwright UI E2E failed with exit code $LASTEXITCODE"
        }
    } finally {
        Remove-Item Env:\UI_E2E_BASE_URL -ErrorAction SilentlyContinue
        Remove-Item Env:\UI_E2E_API_URL -ErrorAction SilentlyContinue
        Pop-Location
    }

    Write-Host ""
    Write-Host "UI E2E test passed."
} finally {
    if (-not $KeepServices) {
        Stop-PortListener -Port $FrontendPort
        Stop-PortListener -Port $BackendPort
    }
}
