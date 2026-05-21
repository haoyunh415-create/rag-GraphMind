[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8201,
    [string]$BackendUrl = "",
    [int]$TimeoutSeconds = 60,
    [switch]$UseRunningBackend,
    [switch]$KeepBackend,
    [switch]$KeepDocuments,
    [switch]$RequireGraph,
    [switch]$SkipGraphSeed,
    [string]$Neo4jUri = "",
    [string]$Neo4jUser = "",
    [string]$Neo4jPassword = "",
    [string]$ReportPath = ""
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$DataDir = Join-Path $Root ".e2e-data"
$DbPath = Join-Path $DataDir "graph-rag-compare.db"
$BackendLog = Join-Path $Root "graph-rag-compare-backend.log"

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
    $ReportPath = Join-Path $DataDir "graph-rag-compare-report.json"
}

$startedProcesses = @()
$documentIds = New-Object System.Collections.Generic.List[string]
$tempFiles = New-Object System.Collections.Generic.List[string]
$runId = (Get-Date -Format "yyyyMMddHHmmss")
$company = "NovaPay$runId"
$owner = "OrionLabs$runId"
$location = "Singapore$runId"
$originalEnv = @{
    SQLITE_DB_PATH = $env:SQLITE_DB_PATH
    EMBEDDING_MODEL = $env:EMBEDDING_MODEL
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    NEO4J_URI = $env:NEO4J_URI
    NEO4J_USER = $env:NEO4J_USER
    NEO4J_PASSWORD = $env:NEO4J_PASSWORD
}

function Write-Step {
    param([string]$Message)
    Write-Host "[graph-compare] $Message"
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )
    if (-not $Condition) {
        throw $Message
    }
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
                Write-Step "$Name ready: HTTP $($response.StatusCode)"
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

function Invoke-CheckedJson {
    param(
        [string]$Name,
        [string]$Url
    )

    $response = Wait-Http -Name $Name -Url $Url -Seconds $TimeoutSeconds
    try {
        return $response.Content | ConvertFrom-Json
    } catch {
        throw "$Name did not return valid JSON"
    }
}

function Start-ServiceProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string]$Arguments,
        [string]$WorkingDirectory,
        [string]$LogPath
    )

    if (Test-Path -LiteralPath $LogPath) {
        Remove-Item -LiteralPath $LogPath -Force
    }
    $stderrLog = "$LogPath.err"
    if (Test-Path -LiteralPath $stderrLog) {
        Remove-Item -LiteralPath $stderrLog -Force
    }

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $FilePath
    $psi.Arguments = $Arguments
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true

    Write-Step "Starting $Name"
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $psi
    $process.EnableRaisingEvents = $true
    $null = $process.Start()

    Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -Action {
        if ($EventArgs.Data) {
            Add-Content -LiteralPath $Event.MessageData.StdoutLog -Value $EventArgs.Data
        }
    } -MessageData @{ StdoutLog = $LogPath } | Out-Null
    Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data) {
            Add-Content -LiteralPath $Event.MessageData.StderrLog -Value $EventArgs.Data
        }
    } -MessageData @{ StderrLog = $stderrLog } | Out-Null
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()

    $script:startedProcesses += $process
}

function Read-SseEvents {
    param([string]$Raw)

    $events = @()
    $blocks = $Raw -split "(`r?`n){2,}"
    foreach ($block in $blocks) {
        $dataLines = @()
        foreach ($line in ($block -split "`r?`n")) {
            if ($line.StartsWith("data: ")) {
                $dataLines += $line.Substring(6)
            }
        }
        if ($dataLines.Count -eq 0) {
            continue
        }
        $json = $dataLines -join "`n"
        try {
            $events += ($json | ConvertFrom-Json)
        } catch {
            throw "Failed to parse SSE data as JSON: $json"
        }
    }
    return $events
}

function Upload-TextDocument {
    param(
        [string]$FileName,
        [string]$Content
    )

    $path = Join-Path $DataDir $FileName
    Set-Content -LiteralPath $path -Encoding UTF8 -Value $Content
    $script:tempFiles.Add($path) | Out-Null

    $uploadRaw = & curl.exe -s --max-time 45 -X POST -F "file=@$path;type=text/plain" "$BackendUrl/api/documents/upload"
    if ($LASTEXITCODE -ne 0) {
        throw "curl upload failed for $FileName with exit code $LASTEXITCODE"
    }
    $upload = $uploadRaw | ConvertFrom-Json
    Assert-True ($upload.status -ne "error") "Upload returned error: $uploadRaw"
    $script:documentIds.Add([string]$upload.document_id) | Out-Null
    $readyDocument = Wait-DocumentReady -DocumentId ([string]$upload.document_id)
    Assert-True ($readyDocument.chunk_count -gt 0) "Upload did not produce chunks: $($readyDocument | ConvertTo-Json -Compress)"
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

function Get-DocumentChunks {
    param([string]$DocumentId)
    $chunksResponse = Invoke-CheckedJson -Name "chunks $DocumentId" -Url "$BackendUrl/api/documents/$DocumentId/chunks"
    return @($chunksResponse.chunks)
}

function Test-TcpOpen {
    param([string]$UriString)

    try {
        $uri = [System.Uri]$UriString
        $hostName = $uri.Host
        $port = if ($uri.Port -gt 0) { $uri.Port } else { 7687 }
        $client = [System.Net.Sockets.TcpClient]::new()
        $async = $client.BeginConnect($hostName, $port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(1200)
        if ($connected) {
            $client.EndConnect($async)
        }
        $client.Close()
        return $connected
    } catch {
        return $false
    }
}

function Invoke-GraphSeed {
    param(
        [array]$OwnerChunks,
        [array]$LocationChunks
    )

    if ($SkipGraphSeed) {
        return @{ ok = $false; detail = "Skipped by -SkipGraphSeed" }
    }
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        return @{ ok = $false; detail = "Backend python not found: $BackendPython" }
    }
    if (-not (Test-TcpOpen -UriString $Neo4jUri)) {
        return @{ ok = $false; detail = "Neo4j port not reachable at $Neo4jUri" }
    }

    $seedChunks = @()
    foreach ($chunk in $OwnerChunks) {
        $seedChunks += @{
            id = $chunk.chunk_id
            document_id = $chunk.document_id
            text = $chunk.text
            chunk_index = $chunk.chunk_index
            entities = @($company, $owner)
        }
    }
    foreach ($chunk in $LocationChunks) {
        $seedChunks += @{
            id = $chunk.chunk_id
            document_id = $chunk.document_id
            text = $chunk.text
            chunk_index = $chunk.chunk_index
            entities = @($owner, $location)
        }
    }

    $payload = @{
        neo4j_uri = $Neo4jUri
        neo4j_user = $Neo4jUser
        neo4j_password = $Neo4jPassword
        company = $company
        owner = $owner
        location = $location
        chunks = $seedChunks
    } | ConvertTo-Json -Depth 20

    $python = @'
import asyncio
import json
import sys
from neo4j import AsyncGraphDatabase

async def main():
    payload = json.loads(sys.stdin.read())
    driver = AsyncGraphDatabase.driver(
        payload["neo4j_uri"],
        auth=(payload["neo4j_user"], payload["neo4j_password"]),
        connection_timeout=4,
        connection_acquisition_timeout=4,
    )
    try:
        async with driver.session() as session:
            await session.run("CREATE CONSTRAINT entity_name IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE")
            await session.run("CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.id IS UNIQUE")

            for chunk in payload["chunks"]:
                await session.run(
                    """
                    MERGE (ch:Chunk {id: $id})
                    SET ch.text = $text,
                        ch.document_id = $document_id,
                        ch.chunk_index = $chunk_index
                    """,
                    id=chunk["id"],
                    text=chunk["text"],
                    document_id=chunk["document_id"],
                    chunk_index=chunk["chunk_index"],
                )
                for entity in chunk["entities"]:
                    entity_type = "LOCATION" if entity == payload["location"] else "ORGANIZATION"
                    await session.run(
                        """
                        MERGE (ent:Entity {name: $name})
                        SET ent.type = $type,
                            ent.id = coalesce(ent.id, randomUUID())
                        WITH ent
                        MATCH (ch:Chunk {id: $chunk_id})
                        MERGE (ent)-[:MENTIONED_IN]->(ch)
                        """,
                        name=entity,
                        type=entity_type,
                        chunk_id=chunk["id"],
                    )

            await session.run(
                """
                MATCH (a:Entity {name: $company})
                MATCH (b:Entity {name: $owner})
                MERGE (a)-[rel:RELATES_TO {type: 'OWNED_BY'}]->(b)
                SET rel.description = 'company owner'
                """,
                company=payload["company"],
                owner=payload["owner"],
            )
            await session.run(
                """
                MATCH (a:Entity {name: $owner})
                MATCH (b:Entity {name: $location})
                MERGE (a)-[rel:RELATES_TO {type: 'HEADQUARTERED_IN'}]->(b)
                SET rel.description = 'owner headquarters'
                """,
                owner=payload["owner"],
                location=payload["location"],
            )
        print(json.dumps({"ok": True, "detail": "seeded graph entities and relations"}))
    finally:
        await driver.close()

asyncio.run(main())
'@

    $seedScriptPath = Join-Path $DataDir ".graph-seed-$runId.py"
    Set-Content -LiteralPath $seedScriptPath -Encoding UTF8 -Value $python
    $script:tempFiles.Add($seedScriptPath) | Out-Null

    try {
        $seedOutput = $payload | & $BackendPython $seedScriptPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            $seedDetail = (($seedOutput | Out-String).Trim() -split "`r?`n" | Select-Object -First 1)
            if (-not $seedDetail) {
                $seedDetail = "graph seed python exited with $LASTEXITCODE"
            }
            return @{ ok = $false; detail = $seedDetail }
        }
        $seedRaw = (($seedOutput | Out-String).Trim() -split "`r?`n" | Select-Object -Last 1)
        return $seedRaw | ConvertFrom-Json
    } catch {
        return @{ ok = $false; detail = $_.Exception.Message }
    }
}

function Ask-KbQuestion {
    param([string]$Question)

    $chatBody = @{
        query = $Question
        mode = "kb"
        top_k = 8
        conversation_id = "graph-compare-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress

    $chatResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -ContentType "application/json" `
        -Body $chatBody `
        -TimeoutSec 90

    Assert-True ($chatResponse.StatusCode -eq 200) "Chat stream returned HTTP $($chatResponse.StatusCode)"
    return @(Read-SseEvents -Raw $chatResponse.Content)
}

function Get-Step {
    param(
        [object]$Trace,
        [string]$Name
    )
    return @($Trace.steps | Where-Object { $_.name -eq $Name })[-1]
}

try {
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend virtualenv python not found: $BackendPython"
    }

    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    if (-not $UseRunningBackend) {
        if (Test-Path -LiteralPath $DbPath) {
            Remove-Item -LiteralPath $DbPath -Force
        }
        Stop-PortListener -Port $BackendPort

        $env:SQLITE_DB_PATH = $DbPath
        $env:EMBEDDING_MODEL = "local-test"
        $env:OPENAI_API_KEY = ""
        $env:NEO4J_URI = $Neo4jUri
        $env:NEO4J_USER = $Neo4jUser
        $env:NEO4J_PASSWORD = $Neo4jPassword

        Start-ServiceProcess `
            -Name "backend" `
            -FilePath $BackendPython `
            -Arguments "-m uvicorn app.main:app --app-dir backend --host $HostName --port $BackendPort" `
            -WorkingDirectory $Root `
            -LogPath $BackendLog
    }

    $health = Invoke-CheckedJson -Name "backend health" -Url "$BackendUrl/api/health"
    Assert-True ($health.status -eq "ok") "Backend health was not ok"

    Write-Step "Uploading graph comparison documents"
    $ownerDoc = Upload-TextDocument `
        -FileName "graph-owner-$runId.txt" `
        -Content @"
Graph RAG comparison document A.
$company is owned by $owner.
$company provides merchant payment orchestration.
"@
    $locationDoc = Upload-TextDocument `
        -FileName "graph-location-$runId.txt" `
        -Content @"
Graph RAG comparison document B.
$owner is headquartered in $location.
$owner operates the $company platform.
"@
    $distractorDoc = Upload-TextDocument `
        -FileName "graph-distractor-$runId.txt" `
        -Content @"
Graph RAG comparison distractor.
Orion is a constellation and Mercury is a chemical element.
This file should not answer the ownership headquarters question.
"@
    Write-Step "Uploaded docs: $($ownerDoc.document_id), $($locationDoc.document_id), $($distractorDoc.document_id)"

    $ownerChunks = @(Get-DocumentChunks -DocumentId $ownerDoc.document_id)
    $locationChunks = @(Get-DocumentChunks -DocumentId $locationDoc.document_id)
    Assert-True ($ownerChunks.Count -gt 0) "Owner document produced no chunks"
    Assert-True ($locationChunks.Count -gt 0) "Location document produced no chunks"

    $seedResult = Invoke-GraphSeed -OwnerChunks $ownerChunks -LocationChunks $locationChunks
    if ($seedResult.ok) {
        Write-Step "Graph seed OK: $($seedResult.detail)"
    } else {
        Write-Step "Graph seed skipped/failed: $($seedResult.detail)"
    }

    $stats = Invoke-CheckedJson -Name "knowledge stats" -Url "$BackendUrl/api/kb/stats"
    Write-Step "KB stats: documents=$($stats.total_documents), chunks=$($stats.total_chunks), entities=$($stats.total_entities), relations=$($stats.total_relations)"

    $question = "Where is $company's owner headquartered?"
    Write-Step "Asking comparison question: $question"
    $events = @(Ask-KbQuestion -Question $question)
    Assert-True ($events.Count -gt 0) "Chat stream returned no SSE events"

    $answer = ((@($events | Where-Object { $_.type -eq "chunk" }) | ForEach-Object { [string]$_.data }) -join "").Trim()
    $citationItems = @()
    foreach ($event in @($events | Where-Object { $_.type -in @("citation", "citations") })) {
        $citationItems += @($event.data)
    }
    $traceEvents = @($events | Where-Object { $_.type -eq "trace" })
    $evaluationEvents = @($events | Where-Object { $_.type -eq "evaluation" })
    Assert-True ($traceEvents.Count -gt 0) "Chat stream returned no trace event"
    Assert-True ($answer.Length -gt 0) "Answer text was empty"

    $trace = $traceEvents[-1].data
    $backendHealthStep = Get-Step -Trace $trace -Name "backend_health"
    $retrieveStep = Get-Step -Trace $trace -Name "retrieve"
    $rankStep = Get-Step -Trace $trace -Name "rank"
    $evaluateStep = Get-Step -Trace $trace -Name "evaluate"

    $graphAvailable = [bool]($backendHealthStep.backends.graph.available)
    $vectorHits = [int]($retrieveStep.counts.vector)
    $bm25Hits = [int]($retrieveStep.counts.bm25)
    $graphHits = [int]($retrieveStep.counts.graph)
    $regularHits = $vectorHits + $bm25Hits
    $rankedCount = [int]($rankStep.output_count)
    $graphUsed = $graphHits -gt 0
    $qualityScore = $null
    $qualityLabel = $null
    if ($evaluationEvents.Count -gt 0) {
        $qualityScore = $evaluationEvents[-1].data.overall_score
        $qualityLabel = $evaluationEvents[-1].data.label
    } elseif ($evaluateStep) {
        $qualityScore = $evaluateStep.overall_score
        $qualityLabel = $evaluateStep.label
    }

    $result = if ($graphUsed) {
        "Graph RAG advantage observed"
    } elseif ($graphAvailable) {
        "Graph available but no graph hits observed"
    } else {
        "Graph unavailable; this run is a conventional RAG baseline"
    }

    if ($RequireGraph -and -not $graphUsed) {
        throw "Graph was required but did not contribute retrieval results. Result: $result"
    }

    $report = [ordered]@{
        question = $question
        expected_answer_contains = $location
        answer = $answer
        graph_available = $graphAvailable
        graph_seed = $seedResult
        vector_hits = $vectorHits
        bm25_hits = $bm25Hits
        regular_rag_hits = $regularHits
        graph_hits = $graphHits
        ranked_results = $rankedCount
        citation_count = @($citationItems).Count
        quality_score = $qualityScore
        quality_label = $qualityLabel
        result = $result
        trace_query_id = $trace.query_id
        report_path = $ReportPath
    }

    $report | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "Graph RAG comparison result"
    Write-Host "  Question: $question"
    Write-Host "  Expected: answer should identify $location"
    Write-Host "  Conventional RAG hits (vector+BM25): $regularHits"
    Write-Host "    vector=$vectorHits bm25=$bm25Hits"
    Write-Host "  Graph RAG hits: $graphHits"
    Write-Host "  Graph available: $graphAvailable"
    Write-Host "  Graph seeded: $($seedResult.ok)"
    Write-Host "  Ranked results: $rankedCount"
    Write-Host "  Citations: $(@($citationItems).Count)"
    if ($null -ne $qualityScore) {
        Write-Host "  Quality: $([math]::Round([double]$qualityScore * 100))% ($qualityLabel)"
    }
    Write-Host "  Result: $result"
    Write-Host "  Report: $ReportPath"
    Write-Host ""
    Write-Host "Answer:"
    Write-Host $answer
} finally {
    if (-not $KeepDocuments) {
        foreach ($documentId in $documentIds) {
            try {
                Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec 10 | Out-Null
            } catch {
                Write-Step "Warning: cleanup delete failed for ${documentId}: $($_.Exception.Message)"
            }
        }
    }
    foreach ($tempFile in $tempFiles) {
        if (Test-Path -LiteralPath $tempFile) {
            Remove-Item -LiteralPath $tempFile -Force
        }
    }
    if (-not $UseRunningBackend -and -not $KeepBackend) {
        foreach ($process in $startedProcesses) {
            try {
                if ($process -and -not $process.HasExited) {
                    Write-Step "Stopping backend process $($process.Id)"
                    Stop-Process -Id $process.Id -Force -ErrorAction Stop
                }
            } catch {
                Write-Step "Warning: failed to stop process $($process.Id): $($_.Exception.Message)"
            }
        }
        Stop-PortListener -Port $BackendPort
    }
    if (-not $UseRunningBackend) {
        foreach ($key in $originalEnv.Keys) {
            if ($null -eq $originalEnv[$key]) {
                Remove-Item -LiteralPath "Env:\$key" -ErrorAction SilentlyContinue
            } else {
                Set-Item -LiteralPath "Env:\$key" -Value $originalEnv[$key]
            }
        }
    }
}
