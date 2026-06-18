[CmdletBinding()]
param(
    [string]$BackendUrl = "http://127.0.0.1:8001",
    [int]$TimeoutSeconds = 90,
    [int]$TopK = 8,
    [switch]$KeepDocuments
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$DemoDir = Join-Path $Root "demo"
$DocsDir = Join-Path $DemoDir "docs"
$DataDir = Join-Path $Root ".e2e-data"
$ReportPath = Join-Path $DataDir "demo-pack-smoke-report.json"
$ApiToken = $env:API_AUTH_TOKEN
$UploadedDocuments = @()

function Write-Step {
    param([string]$Message)
    Write-Host "[demo-pack] $Message"
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

function Get-ApiHeaders {
    if ([string]::IsNullOrWhiteSpace($ApiToken)) {
        return @{}
    }
    return @{ Authorization = "Bearer $ApiToken" }
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
    return @($events)
}

function Wait-DocumentReady {
    param([string]$DocumentId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = "unknown"
    while ((Get-Date) -lt $deadline) {
        try {
            $document = Invoke-RestMethod `
                -Method Get `
                -Uri "$BackendUrl/api/documents/$DocumentId/status" `
                -Headers (Get-ApiHeaders) `
                -TimeoutSec 10
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

function Upload-DemoDocument {
    param([string]$Path)

    $curlArgs = @("-s", "--max-time", "60", "-X", "POST")
    if (-not [string]::IsNullOrWhiteSpace($ApiToken)) {
        $curlArgs += @("-H", "Authorization: Bearer $ApiToken")
    }
    $curlArgs += @("-F", "file=@$Path;type=text/markdown", "$BackendUrl/api/documents/upload")

    $raw = & curl.exe @curlArgs
    if ($LASTEXITCODE -ne 0) {
        throw "curl upload failed for $Path with exit code $LASTEXITCODE"
    }
    $upload = $raw | ConvertFrom-Json
    Assert-True ($upload.status -ne "error") "Upload returned error for ${Path}: $raw"
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$upload.document_id)) "Upload did not return document_id: $raw"

    $ready = Wait-DocumentReady -DocumentId ([string]$upload.document_id)
    $script:UploadedDocuments += [pscustomobject]@{
        document_id = [string]$upload.document_id
        filename = [string]$upload.filename
        status = [string]$ready.status
    }
    return $script:UploadedDocuments[-1]
}

function Ask-DemoQuestion {
    param([object]$Case)

    $body = @{
        query = [string]$Case.query
        mode = "kb"
        top_k = $TopK
        conversation_id = "demo-pack-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress

    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -Headers (Get-ApiHeaders) `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec $TimeoutSeconds

    Assert-True ($response.StatusCode -eq 200) "Chat stream returned HTTP $($response.StatusCode)"
    $events = @(Read-SseEvents -Raw $response.Content)
    $chunks = @($events | Where-Object { $_.type -eq "chunk" })
    $citationEvents = @($events | Where-Object { $_.type -in @("citation", "citations") })
    $traceEvents = @($events | Where-Object { $_.type -eq "trace" })
    $answer = (($chunks | ForEach-Object { [string]$_.data }) -join "").Trim()

    $citations = @()
    foreach ($event in $citationEvents) {
        $citations += @($event.data)
    }
    $citationNames = @($citations | ForEach-Object { [string]$_.document_name })
    $matchedCitation = @($citationNames | Where-Object { $_ -eq [string]$Case.expected_document })[0]

    Assert-True ($answer.Length -gt 0) "Answer was empty for case $($Case.id)"
    Assert-True ($citations.Count -gt 0) "No citations returned for case $($Case.id)"
    Assert-True (-not [string]::IsNullOrWhiteSpace($matchedCitation)) "Expected citation $($Case.expected_document) not found for case $($Case.id). Got: $($citationNames -join ', ')"
    Assert-True ($traceEvents.Count -gt 0) "No trace returned for case $($Case.id)"

    $combinedEvidence = ($answer + "`n" + (($citations | ForEach-Object { [string]$_.text }) -join "`n"))
    foreach ($term in @($Case.expected_terms)) {
        Assert-True ($combinedEvidence.IndexOf([string]$term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) "Case $($Case.id) did not include expected evidence term '$term'. Answer: $answer"
    }

    $trace = $traceEvents[-1].data
    return [pscustomobject]@{
        id = [string]$Case.id
        query = [string]$Case.query
        answer = $answer
        citation_count = $citations.Count
        expected_document = [string]$Case.expected_document
        total_ms = if ($trace.total_ms -ne $null) { [double]$trace.total_ms } else { 0 }
        trace_steps = @($trace.steps).Count
    }
}

try {
    Assert-True (Test-Path -LiteralPath $DocsDir) "Demo docs directory not found: $DocsDir"
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

    Write-Step "Checking backend health: $BackendUrl"
    $health = Invoke-WebRequest `
        -UseBasicParsing `
        -Uri "$BackendUrl/api/health" `
        -Headers (Get-ApiHeaders) `
        -TimeoutSec 10
    Assert-True ($health.StatusCode -eq 200) "Backend health returned HTTP $($health.StatusCode)"

    Write-Step "Uploading demo documents"
    $docPaths = @(
        (Join-Path $DocsDir "01-commerce-policy.md"),
        (Join-Path $DocsDir "02-support-operations.md"),
        (Join-Path $DocsDir "03-graph-relations.md")
    )
    foreach ($path in $docPaths) {
        Assert-True (Test-Path -LiteralPath $path) "Demo document not found: $path"
        $uploaded = Upload-DemoDocument -Path $path
        Write-Step "Ready: $($uploaded.filename) ($($uploaded.status))"
    }

    $cases = @(
        [pscustomobject]@{
            id = "food-returns"
            query = "食品类商品支持七天无理由退货吗？"
            expected_document = "01-commerce-policy.md"
            expected_terms = @("食品", "不支持")
        },
        [pscustomobject]@{
            id = "bot-handoff"
            query = "如果机器人连续两次未能解决用户的同一个问题，接下来应该发生什么？"
            expected_document = "02-support-operations.md"
            expected_terms = @("工单", "人工客服")
        },
        [pscustomobject]@{
            id = "incident-dependency"
            query = "故障 K-17 通过搜索 API 的依赖关系影响了哪个数据库？"
            expected_document = "03-graph-relations.md"
            expected_terms = @("Atlas", "向量数据库")
        }
    )

    Write-Step "Asking demo questions"
    $results = @()
    foreach ($case in $cases) {
        $result = Ask-DemoQuestion -Case $case
        $results += $result
        Write-Step "OK: $($case.id) | citations=$($result.citation_count) | trace_steps=$($result.trace_steps)"
    }

    $report = [ordered]@{
        backend_url = $BackendUrl
        uploaded_documents = $UploadedDocuments
        cases = $results
        created_at = (Get-Date).ToString("o")
    }
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "Demo Pack smoke test passed."
    Write-Host "  Uploaded documents: $($UploadedDocuments.Count)"
    Write-Host "  Checked questions:  $($results.Count)"
    Write-Host "  Report:             $ReportPath"
} finally {
    if (-not $KeepDocuments -and $UploadedDocuments.Count -gt 0) {
        Write-Step "Cleaning up uploaded demo documents"
        foreach ($doc in $UploadedDocuments) {
            try {
                Invoke-RestMethod `
                    -Method Delete `
                    -Uri "$BackendUrl/api/documents/$($doc.document_id)" `
                    -Headers (Get-ApiHeaders) `
                    -TimeoutSec 20 | Out-Null
            } catch {
                Write-Step "Cleanup skipped for $($doc.document_id): $($_.Exception.Message)"
            }
        }
    }
}
