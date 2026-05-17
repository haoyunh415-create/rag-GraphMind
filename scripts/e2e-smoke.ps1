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
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$NpmCmd = (Get-Command npm.cmd -ErrorAction Stop).Source
$BackendUrl = "http://${HostName}:${BackendPort}"
$FrontendUrl = "http://${HostName}:${FrontendPort}"
$BackendLog = Join-Path $Root "backend-e2e.log"
$FrontendLog = Join-Path $Root "frontend-e2e.log"
$DataDir = Join-Path $Root ".e2e-data"
$DbPath = Join-Path $DataDir "rag-e2e.db"

$startedProcesses = @()
$documentId = $null
$tempPath = $null
$originalEnv = @{
    SQLITE_DB_PATH = $env:SQLITE_DB_PATH
    EMBEDDING_MODEL = $env:EMBEDDING_MODEL
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    NEXT_PUBLIC_API_URL = $env:NEXT_PUBLIC_API_URL
}

function Write-Step {
    param([string]$Message)
    Write-Host "[e2e] $Message"
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

    $stdoutLog = $LogPath
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
    } -MessageData @{ StdoutLog = $stdoutLog } | Out-Null
    Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data) {
            Add-Content -LiteralPath $Event.MessageData.StderrLog -Value $EventArgs.Data
        }
    } -MessageData @{ StderrLog = $stderrLog } | Out-Null
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()

    $script:startedProcesses += $process
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

try {
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend virtualenv python not found: $BackendPython"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDir "package.json"))) {
        throw "Frontend package.json not found: $FrontendDir"
    }

    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    if (Test-Path -LiteralPath $DbPath) {
        Remove-Item -LiteralPath $DbPath -Force
    }

    Stop-PortListener -Port $FrontendPort
    Stop-PortListener -Port $BackendPort

    $env:SQLITE_DB_PATH = $DbPath
    $env:EMBEDDING_MODEL = "local-test"
    $env:OPENAI_API_KEY = ""
    $env:CORS_ORIGINS = $FrontendUrl
    Start-ServiceProcess `
        -Name "backend" `
        -FilePath $BackendPython `
        -Arguments "-m uvicorn app.main:app --app-dir backend --host $HostName --port $BackendPort" `
        -WorkingDirectory $Root `
        -LogPath $BackendLog

    $env:NEXT_PUBLIC_API_URL = $BackendUrl
    Start-ServiceProcess `
        -Name "frontend" `
        -FilePath $NpmCmd `
        -Arguments "run dev -- -H $HostName -p $FrontendPort" `
        -WorkingDirectory $FrontendDir `
        -LogPath $FrontendLog

    $health = Invoke-CheckedJson -Name "backend health" -Url "$BackendUrl/api/health"
    Assert-True ($health.status -eq "ok") "Backend health was not ok"

    $frontendResponse = Wait-Http -Name "frontend" -Url $FrontendUrl -Seconds $TimeoutSeconds
    $cssMatches = [regex]::Matches($frontendResponse.Content, 'href="([^"]*\.css[^"]*)"')
    Assert-True ($cssMatches.Count -gt 0) "Frontend HTML did not include a CSS asset"
    $cssHref = $cssMatches[0].Groups[1].Value
    if ($cssHref.StartsWith("/")) {
        $cssUrl = "$FrontendUrl$cssHref"
    } else {
        $cssUrl = $cssHref
    }
    Wait-Http -Name "frontend css" -Url $cssUrl -Seconds 20 | Out-Null

    Write-Step "Uploading test document"
    $tempName = ".e2e-test-" + ([guid]::NewGuid().ToString("N")) + ".txt"
    $tempPath = Join-Path $Root $tempName
    Set-Content -LiteralPath $tempPath -Encoding UTF8 -Value @"
E2E RAG verification document.
The alpha release validates upload, indexing, citation display, and trace capture.
The recommended demo flow is upload a document, ask a grounded question, review citations, and inspect trace events.
"@

    $uploadRaw = & curl.exe -s --max-time 45 -X POST -F "file=@$tempPath;type=text/plain" "$BackendUrl/api/documents/upload"
    if ($LASTEXITCODE -ne 0) {
        throw "curl upload failed with exit code $LASTEXITCODE"
    }
    $upload = $uploadRaw | ConvertFrom-Json
    Assert-True ($upload.status -in @("ready", "partial", "duplicate")) "Upload returned unexpected status: $uploadRaw"
    Assert-True ($upload.chunk_count -gt 0) "Upload did not produce chunks: $uploadRaw"
    $documentId = $upload.document_id
    Write-Step "Upload OK: $documentId"

    $documentsResponse = Invoke-CheckedJson -Name "knowledge documents" -Url "$BackendUrl/api/kb/documents"
    $documents = @($documentsResponse.documents)
    $matchingDocuments = @($documents | Where-Object { $_.document_id -eq $documentId })
    Assert-True ($matchingDocuments.Count -gt 0) "Uploaded document was not listed"

    $chunksResponse = Invoke-CheckedJson -Name "document chunks" -Url "$BackendUrl/api/documents/$documentId/chunks"
    $chunks = @($chunksResponse.chunks)
    Assert-True ($chunks.Count -gt 0) "Uploaded document has no visible chunks"
    Assert-True (($chunks[0].text -like "*alpha release*") -or ($chunks[0].text -like "*recommended demo flow*")) "Chunk text did not include expected e2e content"

    Write-Step "Asking grounded KB question"
    $chatBody = @{
        query = "According to the uploaded document, what does the alpha release validate?"
        mode = "kb"
        top_k = 5
        conversation_id = "e2e-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress

    $chatResponse = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -ContentType "application/json" `
        -Body $chatBody `
        -TimeoutSec 75

    Assert-True ($chatResponse.StatusCode -eq 200) "Chat stream returned HTTP $($chatResponse.StatusCode)"
    $events = @(Read-SseEvents -Raw $chatResponse.Content)
    Assert-True ($events.Count -gt 0) "Chat stream returned no SSE events"

    $chunksFromChat = @($events | Where-Object { $_.type -eq "chunk" })
    $citationEvents = @($events | Where-Object { $_.type -in @("citation", "citations") })
    $traceEvents = @($events | Where-Object { $_.type -eq "trace" })

    Assert-True ($chunksFromChat.Count -gt 0) "Chat stream returned no answer chunks"
    Assert-True ($citationEvents.Count -gt 0) "Chat stream returned no citation event"
    Assert-True ($traceEvents.Count -gt 0) "Chat stream returned no trace event"

    $citationItems = @()
    foreach ($event in $citationEvents) {
        $citationItems += @($event.data)
    }
    Assert-True ($citationItems.Count -gt 0) "Citation event did not contain citations"
    $matchingCitations = @($citationItems | Where-Object { $_.document_id -eq $documentId })
    Assert-True ($matchingCitations.Count -gt 0) "Citations did not reference uploaded document"

    $trace = $traceEvents[-1].data
    Assert-True ($null -ne $trace.query_id) "Trace did not include query_id"
    Assert-True ($trace.total_ms -ge 0) "Trace did not include total_ms"
    $stepNames = @($trace.steps | ForEach-Object { $_.name })
    foreach ($requiredStep in @("intent", "retrieve", "rank", "generate")) {
        Assert-True ($stepNames -contains $requiredStep) "Trace missing step: $requiredStep"
    }

    $answer = (($chunksFromChat | ForEach-Object { [string]$_.data }) -join "")
    Assert-True ($answer.Length -gt 0) "Answer text was empty"
    Write-Step "Chat OK: $($chunksFromChat.Count) chunks, $($citationItems.Count) citations, $($trace.steps.Count) trace steps"

    Write-Step "Deleting uploaded document"
    $deleteResponse = Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec $TimeoutSeconds
    Assert-True ($deleteResponse.status -in @("deleted", "partial")) "Delete returned unexpected status: $($deleteResponse | ConvertTo-Json -Compress)"
    $documentId = $null

    Write-Host ""
    Write-Host "E2E smoke test passed."
    Write-Host "  Frontend: $FrontendUrl"
    Write-Host "  Backend:  $BackendUrl"
    Write-Host "  Logs:     $BackendLog"
    Write-Host "            $FrontendLog"
} finally {
    if ($documentId) {
        try {
            Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec 10 | Out-Null
        } catch {
            Write-Step "Warning: cleanup delete failed for ${documentId}: $($_.Exception.Message)"
        }
    }
    if ($tempPath -and (Test-Path -LiteralPath $tempPath)) {
        Remove-Item -LiteralPath $tempPath -Force
    }
    if (-not $KeepServices) {
        foreach ($process in $startedProcesses) {
            try {
                if ($process -and -not $process.HasExited) {
                    Write-Step "Stopping service process $($process.Id)"
                    Stop-Process -Id $process.Id -Force -ErrorAction Stop
                }
            } catch {
                Write-Step "Warning: failed to stop process $($process.Id): $($_.Exception.Message)"
            }
        }
        Stop-PortListener -Port $FrontendPort
        Stop-PortListener -Port $BackendPort
    }
    foreach ($key in $originalEnv.Keys) {
        if ($null -eq $originalEnv[$key]) {
            Remove-Item -LiteralPath "Env:\$key" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:\$key" -Value $originalEnv[$key]
        }
    }
}
