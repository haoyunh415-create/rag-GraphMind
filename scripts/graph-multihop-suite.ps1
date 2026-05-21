[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8401,
    [string]$BackendUrl = "",
    [int]$TimeoutSeconds = 60,
    [switch]$UseRunningBackend,
    [switch]$KeepBackend,
    [switch]$KeepDocuments,
    [string]$Neo4jUri = "",
    [string]$Neo4jUser = "",
    [string]$Neo4jPassword = "",
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$DataDir = Join-Path $Root ".e2e-data"
$BackendLog = Join-Path $Root "graph-multihop-backend.log"
$RunId = Get-Date -Format "yyyyMMddHHmmss"

if (-not $BackendUrl) {
    $BackendUrl = "http://${HostName}:${BackendPort}"
}
if (-not $Neo4jUri) {
    $Neo4jUri = $env:NEO4J_URI
}
if (-not $Neo4jUri) {
    $Neo4jUri = "bolt://localhost:7687"
}
if (-not $Neo4jUser) {
    $Neo4jUser = $env:NEO4J_USER
}
if (-not $Neo4jUser) {
    $Neo4jUser = "neo4j"
}
if (-not $Neo4jPassword) {
    $Neo4jPassword = $env:NEO4J_PASSWORD
}
if (-not $Neo4jPassword) {
    $Neo4jPassword = "password"
}
if (-not $ReportPath) {
    $ReportPath = Join-Path $DataDir "graph-multihop-suite-report.json"
}

$StartedProcesses = @()
$DocumentIds = New-Object System.Collections.Generic.List[string]
$TempFiles = New-Object System.Collections.Generic.List[string]
$DbPath = Join-Path $DataDir "graph-multihop-$RunId.db"
$OriginalEnv = @{
    SQLITE_DB_PATH = $env:SQLITE_DB_PATH
    EMBEDDING_MODEL = $env:EMBEDDING_MODEL
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    GRAPH_ENTITY_EXTRACTION_TIMEOUT_SECONDS = $env:GRAPH_ENTITY_EXTRACTION_TIMEOUT_SECONDS
    NEO4J_URI = $env:NEO4J_URI
    NEO4J_USER = $env:NEO4J_USER
    NEO4J_PASSWORD = $env:NEO4J_PASSWORD
    GRAPH_ENTITY_EXTRACTION_SYNC = $env:GRAPH_ENTITY_EXTRACTION_SYNC
}

function Write-Step {
    param([string]$Message)
    Write-Host "[graph-multihop] $Message"
}

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) {
        throw $Message
    }
}

function Stop-PortListener {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {}
        }
}

function Test-TcpOpen {
    param([string]$UriString)
    try {
        $uri = [System.Uri]$UriString
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($uri.Host, $uri.Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(1500)
        if ($connected) {
            $client.EndConnect($async)
        }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Wait-Http {
    param([string]$Name, [string]$Url)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) {
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

function Start-Backend {
    if ($UseRunningBackend) {
        return
    }
    if (Test-Path -LiteralPath $DbPath) {
        Remove-Item -LiteralPath $DbPath -Force
    }
    Stop-PortListener -Port $BackendPort

    $env:SQLITE_DB_PATH = $DbPath
    $env:EMBEDDING_MODEL = "local-test"
    $env:OPENAI_API_KEY = "dummy"
    $env:NEO4J_URI = $Neo4jUri
    $env:NEO4J_USER = $Neo4jUser
    $env:NEO4J_PASSWORD = $Neo4jPassword
    $env:GRAPH_ENTITY_EXTRACTION_SYNC = "true"
    $env:GRAPH_ENTITY_EXTRACTION_TIMEOUT_SECONDS = "8"

    Write-Step "Starting isolated backend"
    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $BackendPython
    $psi.Arguments = "-m uvicorn app.main:app --app-dir backend --host $HostName --port $BackendPort"
    $psi.WorkingDirectory = $Root
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $false
    $psi.RedirectStandardError = $false
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $psi
    $null = $process.Start()
    $script:StartedProcesses += $process
}

function Invoke-CheckedJson {
    param([string]$Name, [string]$Url)
    $response = Wait-Http -Name $Name -Url $Url
    return $response.Content | ConvertFrom-Json
}

function Read-SseEvents {
    param([string]$Raw)
    $events = @()
    foreach ($line in ($Raw -split "`r?`n")) {
        if (-not $line.StartsWith("data: ")) {
            continue
        }
        $json = $line.Substring(6)
        if ($json.Trim()) {
            $events += ($json | ConvertFrom-Json)
        }
    }
    return @($events)
}

function Upload-TextDocument {
    param([string]$FileName, [string]$Content)
    $path = Join-Path $DataDir $FileName
    Set-Content -LiteralPath $path -Encoding UTF8 -Value $Content
    $script:TempFiles.Add($path) | Out-Null

    $raw = & curl.exe -s --max-time 120 -X POST -F "file=@$path;type=text/plain" "$BackendUrl/api/documents/upload"
    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed for $FileName"
    }
    $upload = $raw | ConvertFrom-Json
    Assert-True ($upload.status -ne "error") "Upload returned error: $raw"
    $script:DocumentIds.Add([string]$upload.document_id) | Out-Null
    $readyDocument = Wait-DocumentReady -DocumentId ([string]$upload.document_id)
    Assert-True ($readyDocument.chunk_count -gt 0) "Upload produced no chunks: $($readyDocument | ConvertTo-Json -Compress)"
    Assert-True ($readyDocument.index_statuses.graph -eq "ready") "Graph chunk index was not ready: $($readyDocument | ConvertTo-Json -Compress)"
    Assert-True ($readyDocument.index_statuses.graph_extract -eq "ready") "Graph extraction was not ready: $($readyDocument | ConvertTo-Json -Compress)"
    return $upload
}

function Wait-DocumentReady {
    param([string]$DocumentId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = "unknown"
    while ((Get-Date) -lt $deadline) {
        try {
            $document = Invoke-RestMethod -Method Get -Uri "$BackendUrl/api/documents/$DocumentId/status" -TimeoutSec 10
            $lastStatus = [string]$document.status
            if ($lastStatus -in @("ready", "partial", "duplicate")) {
                return $document
            }
            if ($lastStatus -eq "error") {
                throw "Document ingestion failed: $($document.errors -join '; ')"
            }
        } catch {
            if ($_.Exception.Message -like "Document ingestion failed:*") {
                throw
            }
            $lastStatus = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }

    throw "Document ingestion did not become ready: $DocumentId. Last status: $lastStatus"
}

function Ask-KbQuestion {
    param([string]$Question)
    $body = @{
        query = $Question
        mode = "kb"
        top_k = 8
        conversation_id = "graph-multihop-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec 90
    return @(Read-SseEvents -Raw $response.Content)
}

function Get-Step {
    param([object]$Trace, [string]$Name)
    return @($Trace.steps | Where-Object { $_.name -eq $Name })[-1]
}

function Test-ExpectedEvidence {
    param([object]$Trace, [string]$Expected)
    $rank = Get-Step -Trace $Trace -Name "rank"
    $cite = Get-Step -Trace $Trace -Name "cite"
    $items = @($rank.results) + @($cite.results)
    foreach ($item in $items) {
        $text = [string]$item.text
        if ($text.IndexOf($Expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
        $path = @($item.graph_context.path_entities) -join " "
        if ($path.IndexOf($Expected, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

try {
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend virtualenv python not found: $BackendPython"
    }
    if (-not (Test-TcpOpen -UriString $Neo4jUri)) {
        throw "Neo4j is required for this suite but is not reachable at $Neo4jUri"
    }

    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    Start-Backend
    $health = Invoke-CheckedJson -Name "backend health" -Url "$BackendUrl/api/health"
    Assert-True ($health.status -eq "ok") "Backend health was not ok"

    $company = "NovaPay$RunId"
    $owner = "OrionLabs$RunId"
    $location = "Singapore$RunId"
    $product = "RiskGateway$RunId"
    $team = "AegisTeam$RunId"
    $manager = "MiraChen$RunId"
    $incident = "Incident$RunId"
    $service = "CheckoutService$RunId"
    $database = "LedgerDB$RunId"

    Write-Step "Uploading controlled multi-hop documents"
    Upload-TextDocument -FileName "mh-owner-$RunId.txt" -Content "$company is owned by $owner." | Out-Null
    Upload-TextDocument -FileName "mh-location-$RunId.txt" -Content "$owner is headquartered in $location." | Out-Null
    Upload-TextDocument -FileName "mh-product-$RunId.txt" -Content "$team maintains $product." | Out-Null
    Upload-TextDocument -FileName "mh-manager-$RunId.txt" -Content "$manager manages $team." | Out-Null
    Upload-TextDocument -FileName "mh-incident-$RunId.txt" -Content "$incident affects $service." | Out-Null
    Upload-TextDocument -FileName "mh-dependency-$RunId.txt" -Content "$service depends on $database." | Out-Null

    $stats = Invoke-CheckedJson -Name "kb stats" -Url "$BackendUrl/api/kb/stats"
    Assert-True ($stats.total_entities -ge 9) "Expected extracted graph entities, got $($stats.total_entities)"
    Assert-True ($stats.total_relations -ge 6) "Expected extracted graph relations, got $($stats.total_relations)"

    $cases = @(
        @{
            id = "owner-headquarters"
            question = "Where is `"$company`" owner's headquarters?"
            expected = $location
        },
        @{
            id = "product-manager"
            question = "Who manages the team that maintains `"$product`"?"
            expected = $manager
        },
        @{
            id = "incident-dependency"
            question = "Which database is connected to `"$incident`" through the affected service?"
            expected = $database
        }
    )

    $rows = @()
    foreach ($case in $cases) {
        Write-Step "Asking $($case.id)"
        $events = Ask-KbQuestion -Question $case.question
        $trace = @($events | Where-Object { $_.type -eq "trace" })[-1].data
        Assert-True ($null -ne $trace) "No trace event for $($case.id)"
        $retrieve = Get-Step -Trace $trace -Name "retrieve"
        $graphHits = [int]$retrieve.counts.graph
        $expectedHit = Test-ExpectedEvidence -Trace $trace -Expected $case.expected
        $pass = ($graphHits -gt 0) -and $expectedHit
        $rows += [ordered]@{
            id = $case.id
            question = $case.question
            expected = $case.expected
            graph_hits = $graphHits
            expected_evidence_hit = $expectedHit
            pass = $pass
            query_id = $trace.query_id
        }
    }

    $passCount = @($rows | Where-Object { $_.pass }).Count
    $report = [ordered]@{
        created_at = (Get-Date).ToString("s")
        neo4j_uri = $Neo4jUri
        entity_count = $stats.total_entities
        relation_count = $stats.total_relations
        pass_count = $passCount
        case_count = $rows.Count
        rows = $rows
    }
    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "Graph multi-hop suite result"
    Write-Host "  Entities: $($stats.total_entities)"
    Write-Host "  Relations: $($stats.total_relations)"
    Write-Host "  Passed: $passCount/$($rows.Count)"
    Write-Host "  Report: $ReportPath"

    if ($passCount -ne $rows.Count) {
        throw "Graph multi-hop suite failed: $passCount/$($rows.Count) passed"
    }
} finally {
    if (-not $KeepDocuments) {
        foreach ($documentId in $DocumentIds) {
            try {
                Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec 10 | Out-Null
            } catch {
                Write-Step "Warning: cleanup delete failed for ${documentId}: $($_.Exception.Message)"
            }
        }
    }
    foreach ($tempFile in $TempFiles) {
        if (Test-Path -LiteralPath $tempFile) {
            Remove-Item -LiteralPath $tempFile -Force
        }
    }
    if (-not $UseRunningBackend -and -not $KeepBackend) {
        foreach ($process in $StartedProcesses) {
            try {
                if ($process -and -not $process.HasExited) {
                    Stop-Process -Id $process.Id -Force -ErrorAction Stop
                }
            } catch {}
        }
        Stop-PortListener -Port $BackendPort
    }
    if (-not $UseRunningBackend) {
        foreach ($key in $OriginalEnv.Keys) {
            if ($null -eq $OriginalEnv[$key]) {
                Remove-Item -LiteralPath "Env:\$key" -ErrorAction SilentlyContinue
            } else {
                Set-Item -LiteralPath "Env:\$key" -Value $OriginalEnv[$key]
            }
        }
    }
}
