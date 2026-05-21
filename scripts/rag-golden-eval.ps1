[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8501,
    [string]$BackendUrl = "",
    [string]$GoldenPath = "eval\golden-rag-cases.json",
    [string]$ReportPath = "",
    [string]$MarkdownPath = "",
    [int]$TimeoutSeconds = 60,
    [int]$TopK = 8,
    [switch]$UseRunningBackend,
    [switch]$KeepBackend,
    [switch]$KeepDocuments,
    [switch]$UseRealLlm,
    [string[]]$Strategies = @("vector", "reranker"),
    [double]$MinRecallAtK = 1.0,
    [double]$MinCitationPrecision = 0.95,
    [double]$MinRefusalAccuracy = 1.0,
    [double]$MinBehaviorPassRate = 1.0,
    [double]$MaxLatencyP95Ms = 8000.0,
    [bool]$FailOnPerformanceWarnings = $true,
    [switch]$AllowPerformanceWarnings
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$DataDir = Join-Path $Root ".e2e-data"
$RunId = Get-Date -Format "yyyyMMddHHmmss"

if (-not [System.IO.Path]::IsPathRooted($GoldenPath)) {
    $GoldenPath = Join-Path $Root $GoldenPath
}
if (-not $BackendUrl) {
    $BackendUrl = "http://${HostName}:${BackendPort}"
}
if (-not $ReportPath) {
    $ReportPath = Join-Path $DataDir "rag-golden-eval-report.json"
}
if (-not $MarkdownPath) {
    $MarkdownPath = Join-Path $DataDir "rag-golden-eval-report.md"
}

$OriginalEnv = @{
    SQLITE_DB_PATH = $env:SQLITE_DB_PATH
    EMBEDDING_MODEL = $env:EMBEDDING_MODEL
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    RERANKER_ENABLED = $env:RERANKER_ENABLED
    API_AUTH_TOKEN = $env:API_AUTH_TOKEN
}
$GateFailOnPerformanceWarnings = $FailOnPerformanceWarnings -and -not $AllowPerformanceWarnings

function Write-Step {
    param([string]$Message)
    Write-Host "[rag-golden] $Message"
}

function Stop-PortListener {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique |
        ForEach-Object {
            try { Stop-Process -Id $_ -Force -ErrorAction Stop } catch {}
        }
}

function Wait-Http {
    param([string]$Url)
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
    throw "Service did not become ready: $Url. Last error: $lastError"
}

function Start-IsolatedBackend {
    param(
        [string]$DbPath,
        [bool]$RerankerEnabled
    )

    Stop-PortListener -Port $BackendPort
    if (Test-Path -LiteralPath $DbPath) {
        Remove-Item -LiteralPath $DbPath -Force
    }

    $env:SQLITE_DB_PATH = $DbPath
    $env:EMBEDDING_MODEL = "local-test"
    $env:RERANKER_ENABLED = if ($RerankerEnabled) { "true" } else { "false" }
    $env:API_AUTH_TOKEN = ""
    if ($UseRealLlm) {
        if ($null -eq $OriginalEnv.OPENAI_API_KEY) {
            Remove-Item -LiteralPath "Env:\OPENAI_API_KEY" -ErrorAction SilentlyContinue
        } else {
            $env:OPENAI_API_KEY = $OriginalEnv.OPENAI_API_KEY
        }
    } else {
        $env:OPENAI_API_KEY = ""
    }

    $process = Start-Process `
        -FilePath $BackendPython `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", $HostName, "--port", "$BackendPort") `
        -WorkingDirectory $Root `
        -WindowStyle Hidden `
        -PassThru

    Wait-Http -Url "$BackendUrl/api/health" | Out-Null
    return $process
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

function Test-ContainsTerm {
    param([string]$Text, [string]$Term)
    if (-not $Text -or -not $Term) {
        return $false
    }
    return $Text.IndexOf($Term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-Step {
    param([object]$Trace, [string]$Name)
    return @($Trace.steps | Where-Object { $_.name -eq $Name })[-1]
}

function Get-ResultDocumentId {
    param([object]$Result)
    return [string]($Result.document_id)
}

function Get-PerformanceWarnings {
    param([object]$Trace)
    if (-not $Trace) {
        return @()
    }
    $warnings = @()
    if ($Trace.timings -and $Trace.timings.performance_warnings) {
        $warnings += @($Trace.timings.performance_warnings)
    } elseif ($Trace.steps) {
        foreach ($step in @($Trace.steps)) {
            if ($step.performance_warnings) {
                $warnings += @($step.performance_warnings)
            }
        }
    }
    return @($warnings)
}

function Test-NoAnswerPass {
    param([string]$Answer, [int]$CitationCount)
    if ($CitationCount -eq 0) {
        return $true
    }
    foreach ($phrase in @("not found", "not enough", "insufficient", "cannot answer", "unable to answer", "no relevant", "无法", "没有")) {
        if (Test-ContainsTerm -Text $Answer -Term $phrase) {
            return $true
        }
    }
    return $false
}

function Upload-TextDocument {
    param([object]$Document)
    $path = Join-Path $DataDir ("$RunId-$($Document.filename)")
    Set-Content -LiteralPath $path -Encoding UTF8 -Value ([string]$Document.text)

    $raw = & curl.exe -s --max-time 45 -X POST -F "file=@$path;type=text/plain" "$BackendUrl/api/documents/upload"
    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed: $($Document.filename)"
    }
    $upload = $raw | ConvertFrom-Json
    Wait-DocumentReady -DocumentId ([string]$upload.document_id) | Out-Null
    return [ordered]@{
        golden_id = [string]$Document.id
        upload = $upload
    }
}

function Wait-DocumentReady {
    param([string]$DocumentId)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = "unknown"
    while ((Get-Date) -lt $deadline) {
        try {
            $doc = Invoke-RestMethod -Method Get -Uri "$BackendUrl/api/documents/$DocumentId/status" -TimeoutSec 10
            $lastStatus = [string]$doc.status
            if ($lastStatus -in @("ready", "partial", "duplicate")) {
                return $doc
            }
            if ($lastStatus -eq "error") {
                throw "Document ingestion failed for ${DocumentId}: $($doc.errors -join '; ')"
            }
        } catch {
            if ($_.Exception.Message -like "Document ingestion failed*") {
                throw
            }
            $lastStatus = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Document ingestion did not become ready: $DocumentId. Last status: $lastStatus"
}

function Ask-Question {
    param([object]$Case)
    $body = @{
        query = [string]$Case.query
        mode = "kb"
        top_k = $TopK
        conversation_id = "rag-golden-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress
    $started = Get-Date
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
        -TimeoutSec 90
    $latencyMs = ((Get-Date) - $started).TotalMilliseconds
    return @{
        events = @(Read-SseEvents -Raw $response.Content)
        latency_ms = [math]::Round($latencyMs, 1)
    }
}

function Measure-Case {
    param(
        [object]$Case,
        [hashtable]$DocumentIdMap
    )
    $asked = Ask-Question -Case $Case
    $events = @($asked.events)
    $answer = ((@($events | Where-Object { $_.type -eq "chunk" }) | ForEach-Object { [string]$_.data }) -join "").Trim()
    $trace = @($events | Where-Object { $_.type -eq "trace" })[-1].data
    $evaluation = @($events | Where-Object { $_.type -eq "evaluation" })[-1].data
    $rankStep = Get-Step -Trace $trace -Name "rank"
    $citeStep = Get-Step -Trace $trace -Name "cite"
    $ranked = @($rankStep.results)
    $citations = @($citeStep.results)
    $expectedGoldenId = [string]$Case.expected_document_id
    $expectedDocumentId = if ($expectedGoldenId) { [string]$DocumentIdMap[$expectedGoldenId] } else { "" }
    $expectedNoAnswer = [bool]$Case.expected_no_answer
    $performanceWarnings = @(Get-PerformanceWarnings -Trace $trace)

    $rank = 0
    if ($expectedDocumentId) {
        for ($i = 0; $i -lt $ranked.Count; $i++) {
            if ((Get-ResultDocumentId -Result $ranked[$i]) -eq $expectedDocumentId) {
                $rank = $i + 1
                break
            }
        }
    }
    $recallAtK = $rank -gt 0
    $mrr = if ($rank -gt 0) { 1.0 / $rank } else { 0.0 }
    $citationHitCount = 0
    if ($expectedDocumentId) {
        $citationHitCount = @($citations | Where-Object { (Get-ResultDocumentId -Result $_) -eq $expectedDocumentId }).Count
    }
    $citationPrecision = if ($citations.Count -gt 0) {
        [double]$citationHitCount / [double]$citations.Count
    } elseif ($expectedNoAnswer) {
        1.0
    } else {
        0.0
    }
    $termHits = @($Case.expected_terms | Where-Object { Test-ContainsTerm -Text $answer -Term ([string]$_) }).Count
    $refusalPass = if ($expectedNoAnswer) {
        Test-NoAnswerPass -Answer $answer -CitationCount $citations.Count
    } else {
        $false
    }
    $behaviorPass = if ($expectedNoAnswer) {
        $refusalPass
    } else {
        $recallAtK -and ($citationHitCount -gt 0) -and ($termHits -gt 0)
    }

    return [ordered]@{
        id = [string]$Case.id
        difficulty = [string]$Case.difficulty
        query = [string]$Case.query
        expected_document_id = $expectedGoldenId
        uploaded_document_id = $expectedDocumentId
        expected_no_answer = $expectedNoAnswer
        recall_at_k = $recallAtK
        rank = $rank
        reciprocal_rank = $mrr
        citation_precision = [math]::Round($citationPrecision, 3)
        citation_count = $citations.Count
        expected_term_hits = $termHits
        expected_term_count = @($Case.expected_terms).Count
        refusal_pass = $refusalPass
        behavior_pass = $behaviorPass
        quality_score = if ($evaluation) { $evaluation.overall_score } else { $null }
        quality_label = if ($evaluation) { $evaluation.label } else { $null }
        latency_ms = $asked.latency_ms
        performance_warning_count = $performanceWarnings.Count
        performance_warnings = @($performanceWarnings)
        answer = $answer
    }
}

function Average {
    param([array]$Values)
    if (-not $Values -or $Values.Count -eq 0) {
        return 0.0
    }
    return [math]::Round((($Values | Measure-Object -Average).Average), 3)
}

function Percent {
    param([int]$Numerator, [int]$Denominator)
    if ($Denominator -le 0) {
        return 0.0
    }
    return [math]::Round([double]$Numerator / [double]$Denominator, 3)
}

function Percentile {
    param([double[]]$Values, [double]$P)
    if (-not $Values -or $Values.Count -eq 0) {
        return 0.0
    }
    $sorted = @($Values | Sort-Object)
    $index = [math]::Ceiling(($P / 100.0) * $sorted.Count) - 1
    $index = [math]::Max(0, [math]::Min($sorted.Count - 1, $index))
    return [math]::Round([double]$sorted[$index], 1)
}

function Summarize-Rows {
    param([array]$Rows)
    $answerRows = @($Rows | Where-Object { -not $_.expected_no_answer })
    $negativeRows = @($Rows | Where-Object { $_.expected_no_answer })
    return [ordered]@{
        case_count = $Rows.Count
        answer_case_count = $answerRows.Count
        negative_case_count = $negativeRows.Count
        recall_at_k = Percent (@($answerRows | Where-Object { $_.recall_at_k }).Count) $answerRows.Count
        mrr = Average @($answerRows | ForEach-Object { [double]$_.reciprocal_rank })
        citation_precision = Average @($Rows | ForEach-Object { [double]$_.citation_precision })
        refusal_accuracy = Percent (@($negativeRows | Where-Object { $_.refusal_pass }).Count) $negativeRows.Count
        behavior_pass_rate = Percent (@($Rows | Where-Object { $_.behavior_pass }).Count) $Rows.Count
        quality_average = Average @($Rows | Where-Object { $null -ne $_.quality_score } | ForEach-Object { [double]$_.quality_score })
        latency_p50_ms = Percentile @($Rows | ForEach-Object { [double]$_.latency_ms }) 50
        latency_p95_ms = Percentile @($Rows | ForEach-Object { [double]$_.latency_ms }) 95
        performance_warning_count = (@($Rows | ForEach-Object { [int]$_.performance_warning_count }) | Measure-Object -Sum).Sum
    }
}

function Test-Gates {
    param([array]$StrategyReports)
    $failures = New-Object System.Collections.Generic.List[object]
    foreach ($strategy in @($StrategyReports)) {
        $s = $strategy.summary
        $name = [string]$strategy.strategy
        if ([double]$s.recall_at_k -lt $MinRecallAtK) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "recall_at_k"
                actual = [double]$s.recall_at_k
                threshold = $MinRecallAtK
                direction = "min"
            }) | Out-Null
        }
        if ([double]$s.citation_precision -lt $MinCitationPrecision) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "citation_precision"
                actual = [double]$s.citation_precision
                threshold = $MinCitationPrecision
                direction = "min"
            }) | Out-Null
        }
        if ([double]$s.refusal_accuracy -lt $MinRefusalAccuracy) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "refusal_accuracy"
                actual = [double]$s.refusal_accuracy
                threshold = $MinRefusalAccuracy
                direction = "min"
            }) | Out-Null
        }
        if ([double]$s.behavior_pass_rate -lt $MinBehaviorPassRate) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "behavior_pass_rate"
                actual = [double]$s.behavior_pass_rate
                threshold = $MinBehaviorPassRate
                direction = "min"
            }) | Out-Null
        }
        if ([double]$s.latency_p95_ms -gt $MaxLatencyP95Ms) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "latency_p95_ms"
                actual = [double]$s.latency_p95_ms
                threshold = $MaxLatencyP95Ms
                direction = "max"
            }) | Out-Null
        }
        if ($GateFailOnPerformanceWarnings -and [int]$s.performance_warning_count -gt 0) {
            $failures.Add([ordered]@{
                strategy = $name
                metric = "performance_warning_count"
                actual = [int]$s.performance_warning_count
                threshold = 0
                direction = "max"
            }) | Out-Null
        }
    }
    return $failures.ToArray()
}

function Run-Strategy {
    param([string]$StrategyName, [object]$Golden)
    $rerankerEnabled = $StrategyName -ne "vector"
    $process = $null
    $documentIds = New-Object System.Collections.Generic.List[string]
    $dbPath = Join-Path $DataDir "rag-golden-$StrategyName-$RunId.db"

    try {
        if (-not $UseRunningBackend) {
            $process = Start-IsolatedBackend -DbPath $dbPath -RerankerEnabled $rerankerEnabled
        }

        Write-Step "Running strategy: $StrategyName"
        $documentIdMap = @{}
        foreach ($doc in @($Golden.documents)) {
            $row = Upload-TextDocument -Document $doc
            $documentIdMap[$row.golden_id] = [string]$row.upload.document_id
            $documentIds.Add([string]$row.upload.document_id) | Out-Null
        }

        $rows = @()
        foreach ($case in @($Golden.cases)) {
            $rows += Measure-Case -Case $case -DocumentIdMap $documentIdMap
        }

        return [ordered]@{
            strategy = $StrategyName
            reranker_enabled = $rerankerEnabled
            summary = Summarize-Rows -Rows $rows
            rows = $rows
        }
    } finally {
        if (-not $KeepDocuments) {
            foreach ($documentId in $documentIds) {
                try {
                    Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec 10 | Out-Null
                } catch {}
            }
        }
        if (-not $UseRunningBackend -and -not $KeepBackend) {
            if ($process -and -not $process.HasExited) {
                Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
            }
            Stop-PortListener -Port $BackendPort
        }
    }
}

function Write-MarkdownReport {
    param([object]$Report, [string]$Path)
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# RAG Golden Evaluation") | Out-Null
    $lines.Add("") | Out-Null
    $lines.Add("- Created at: $($Report.created_at)") | Out-Null
    $lines.Add("- Golden set: ``$($Report.golden_path)``") | Out-Null
    $lines.Add("- LLM mode: $($Report.llm_mode)") | Out-Null
    $lines.Add("- Gate status: $($Report.gates.status)") | Out-Null
    $lines.Add("") | Out-Null
    $lines.Add("| Strategy | Recall@K | MRR | Citation Precision | Refusal Accuracy | Behavior Pass | Latency p50 | Latency p95 | Perf Warnings |") | Out-Null
    $lines.Add("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |") | Out-Null
    foreach ($strategy in @($Report.strategies)) {
        $s = $strategy.summary
        $lines.Add("| $($strategy.strategy) | $([math]::Round($s.recall_at_k * 100))% | $([math]::Round($s.mrr, 3)) | $([math]::Round($s.citation_precision * 100))% | $([math]::Round($s.refusal_accuracy * 100))% | $([math]::Round($s.behavior_pass_rate * 100))% | $($s.latency_p50_ms) ms | $($s.latency_p95_ms) ms | $($s.performance_warning_count) |") | Out-Null
    }
    $lines.Add("") | Out-Null
    $lines.Add("## Gates") | Out-Null
    $lines.Add("") | Out-Null
    $gates = $Report.gates
    $lines.Add("- Recall@K >= $($gates.thresholds.min_recall_at_k)") | Out-Null
    $lines.Add("- Citation Precision >= $($gates.thresholds.min_citation_precision)") | Out-Null
    $lines.Add("- Refusal Accuracy >= $($gates.thresholds.min_refusal_accuracy)") | Out-Null
    $lines.Add("- Behavior Pass >= $($gates.thresholds.min_behavior_pass_rate)") | Out-Null
    $lines.Add("- Latency p95 <= $($gates.thresholds.max_latency_p95_ms) ms") | Out-Null
    $lines.Add("- Performance warnings must be zero: $($gates.thresholds.fail_on_performance_warnings)") | Out-Null
    if (@($gates.failures).Count -eq 0) {
        $lines.Add("- Result: pass") | Out-Null
    } else {
        $lines.Add("- Result: fail") | Out-Null
        foreach ($failure in @($gates.failures)) {
            $lines.Add("- $($failure.strategy) / $($failure.metric): actual=$($failure.actual), threshold=$($failure.threshold), direction=$($failure.direction)") | Out-Null
        }
    }
    $lines.Add("") | Out-Null
    $lines.Add("## Failed Or Weak Cases") | Out-Null
    foreach ($strategy in @($Report.strategies)) {
        $weakRows = @($strategy.rows | Where-Object { -not $_.behavior_pass })
        if ($weakRows.Count -eq 0) {
            $lines.Add("- $($strategy.strategy): none") | Out-Null
            continue
        }
        foreach ($row in $weakRows) {
            $lines.Add("- $($strategy.strategy) / $($row.id): recall=$($row.recall_at_k), citation_precision=$($row.citation_precision), refusal=$($row.refusal_pass)") | Out-Null
        }
    }
    Set-Content -LiteralPath $Path -Encoding UTF8 -Value $lines
}

try {
    if (-not (Test-Path -LiteralPath $GoldenPath)) {
        throw "Golden set not found: $GoldenPath"
    }
    if (-not $UseRunningBackend -and -not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend Python not found: $BackendPython"
    }
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
    $golden = Get-Content -LiteralPath $GoldenPath -Raw | ConvertFrom-Json

    $strategyReports = @()
    foreach ($strategy in $Strategies) {
        $strategyReports += Run-Strategy -StrategyName $strategy -Golden $golden
    }
    $gateFailures = @(Test-Gates -StrategyReports $strategyReports)

    $report = [ordered]@{
        created_at = (Get-Date).ToString("s")
        golden_path = $GoldenPath
        llm_mode = if ($UseRealLlm) { "real" } else { "offline" }
        top_k = $TopK
        gates = [ordered]@{
            status = if ($gateFailures.Count -eq 0) { "pass" } else { "fail" }
            thresholds = [ordered]@{
                min_recall_at_k = $MinRecallAtK
                min_citation_precision = $MinCitationPrecision
                min_refusal_accuracy = $MinRefusalAccuracy
                min_behavior_pass_rate = $MinBehaviorPassRate
                max_latency_p95_ms = $MaxLatencyP95Ms
                fail_on_performance_warnings = $GateFailOnPerformanceWarnings
            }
            failures = $gateFailures
        }
        strategies = $strategyReports
    }
    $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $ReportPath -Encoding UTF8
    Write-MarkdownReport -Report $report -Path $MarkdownPath

    Write-Host ""
    Write-Host "RAG golden evaluation"
    foreach ($strategy in $strategyReports) {
        $s = $strategy.summary
        Write-Host "  $($strategy.strategy): Recall@$TopK=$([math]::Round($s.recall_at_k * 100))%, MRR=$([math]::Round($s.mrr, 3)), CitationPrecision=$([math]::Round($s.citation_precision * 100))%, Refusal=$([math]::Round($s.refusal_accuracy * 100))%, Behavior=$([math]::Round($s.behavior_pass_rate * 100))%, p95=$($s.latency_p95_ms)ms, PerfWarnings=$($s.performance_warning_count)"
    }
    Write-Host "  Gate status: $($report.gates.status)"
    Write-Host "  JSON report: $ReportPath"
    Write-Host "  Markdown report: $MarkdownPath"
    if ($gateFailures.Count -gt 0) {
        Write-Host ""
        Write-Host "RAG golden gates failed:"
        foreach ($failure in $gateFailures) {
            Write-Host "  - $($failure.strategy) / $($failure.metric): actual=$($failure.actual), threshold=$($failure.threshold), direction=$($failure.direction)"
        }
        exit 1
    }
} catch {
    Write-Host ""
    Write-Host "RAG golden evaluation failed before report completion:"
    Write-Host "  $($_.Exception.Message)"
    if ($_.InvocationInfo -and $_.InvocationInfo.PositionMessage) {
        Write-Host $_.InvocationInfo.PositionMessage
    }
    if ($_.ScriptStackTrace) {
        Write-Host $_.ScriptStackTrace
    }
    throw
} finally {
    foreach ($key in $OriginalEnv.Keys) {
        if ($null -eq $OriginalEnv[$key]) {
            Remove-Item -LiteralPath "Env:\$key" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:\$key" -Value $OriginalEnv[$key]
        }
    }
}
