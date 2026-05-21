[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$BackendPort = 8301,
    [int]$TimeoutSeconds = 60,
    [string]$ReportPath = "",
    [switch]$KeepDocuments,
    [switch]$UseRealLlm
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendPython = Join-Path $Root "backend\venv\Scripts\python.exe"
$DataDir = Join-Path $Root ".e2e-data"
$RunId = Get-Date -Format "yyyyMMddHHmmss"

if (-not $ReportPath) {
    $ReportPath = Join-Path $DataDir "rag-eval-suite-report.json"
}

$originalEnv = @{
    SQLITE_DB_PATH = $env:SQLITE_DB_PATH
    EMBEDDING_MODEL = $env:EMBEDDING_MODEL
    OPENAI_API_KEY = $env:OPENAI_API_KEY
    RERANKER_ENABLED = $env:RERANKER_ENABLED
}

function Write-Step {
    param([string]$Message)
    Write-Host "[rag-eval] $Message"
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
        [bool]$RerankerEnabled,
        [bool]$UseRealLlmMode
    )

    Stop-PortListener -Port $BackendPort
    if (Test-Path -LiteralPath $DbPath) {
        Remove-Item -LiteralPath $DbPath -Force
    }

    $env:SQLITE_DB_PATH = $DbPath
    $env:EMBEDDING_MODEL = "local-test"
    $env:RERANKER_ENABLED = if ($RerankerEnabled) { "true" } else { "false" }
    if ($UseRealLlmMode) {
        if ($null -eq $originalEnv.OPENAI_API_KEY) {
            Remove-Item -LiteralPath "Env:\OPENAI_API_KEY" -ErrorAction SilentlyContinue
        } else {
            $env:OPENAI_API_KEY = $originalEnv.OPENAI_API_KEY
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

    Wait-Http -Url "http://${HostName}:${BackendPort}/api/health" | Out-Null
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
        if (-not $json.Trim()) {
            continue
        }
        $events += ($json | ConvertFrom-Json)
    }
    return @($events)
}

function Upload-TextDocument {
    param(
        [string]$BackendUrl,
        [string]$FileName,
        [string]$Content
    )
    $path = Join-Path $DataDir $FileName
    Set-Content -LiteralPath $path -Encoding UTF8 -Value $Content

    $raw = & curl.exe -s --max-time 45 -X POST -F "file=@$path;type=text/plain" "$BackendUrl/api/documents/upload"
    if ($LASTEXITCODE -ne 0) {
        throw "Upload failed: $FileName"
    }
    $upload = $raw | ConvertFrom-Json
    Wait-DocumentReady -BackendUrl $BackendUrl -DocumentId ([string]$upload.document_id) | Out-Null
    return $upload
}

function Wait-DocumentReady {
    param(
        [string]$BackendUrl,
        [string]$DocumentId
    )

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

function Ask-Question {
    param(
        [string]$BackendUrl,
        [string]$Question
    )
    $bodyText = @{
        query = $Question
        mode = "kb"
        top_k = 8
        conversation_id = "rag-eval-" + ([guid]::NewGuid().ToString("N"))
    } | ConvertTo-Json -Compress
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyText)
    $response = Invoke-WebRequest `
        -UseBasicParsing `
        -Method Post `
        -Uri "$BackendUrl/api/chat/stream" `
        -ContentType "application/json; charset=utf-8" `
        -Body $bodyBytes `
        -TimeoutSec 90
    return @(Read-SseEvents -Raw $response.Content)
}

function Get-Step {
    param(
        [object]$Trace,
        [string]$Name
    )
    return @($Trace.steps | Where-Object { $_.name -eq $Name })[-1]
}

function Test-ContainsTerm {
    param(
        [string]$Text,
        [string]$Term
    )
    if (-not $Text -or -not $Term) {
        return $false
    }
    return $Text.IndexOf($Term, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-NoAnswerPass {
    param(
        [string]$Answer,
        [int]$CitationCount
    )
    if ($CitationCount -eq 0) {
        return $true
    }
    $phrases = @(
        "not found",
        "not enough",
        "insufficient",
        "cannot answer",
        "can not answer",
        "unable to answer",
        "no relevant"
    )
    foreach ($phrase in $phrases) {
        if (Test-ContainsTerm -Text $Answer -Term $phrase) {
            return $true
        }
    }
    return $false
}

function Test-LlmSuccess {
    param([string]$Answer)
    if (-not $Answer) {
        return $false
    }
    $failureMarkers = @(
        "OPENAI_API_KEY",
        "LLM_BASE_URL",
        "OPENAI_MODEL",
        "Connection error",
        "LLM is not configured"
    )
    foreach ($marker in $failureMarkers) {
        if (Test-ContainsTerm -Text $Answer -Term $marker) {
            return $false
        }
    }
    return $true
}

function Run-Suite {
    param(
        [string]$ModeName,
        [bool]$RerankerEnabled
    )

    $backendUrl = "http://${HostName}:${BackendPort}"
    $dbPath = Join-Path $DataDir "rag-eval-$ModeName-$RunId.db"
    $process = Start-IsolatedBackend -DbPath $dbPath -RerankerEnabled $RerankerEnabled -UseRealLlmMode $UseRealLlm
    $documentIds = New-Object System.Collections.Generic.List[string]

    try {
        Write-Step "Running suite: $ModeName, reranker=$RerankerEnabled"

        $documents = @(
            @{
                name = "returns-$RunId.txt"
                text = "Return policy. Food, custom goods, virtual goods, and opened personal-care items do not support seven-day no-reason returns. Normal goods support seven-day no-reason returns after delivery."
            },
            @{
                name = "invoice-$RunId.txt"
                text = "Invoice process. Users can request an electronic invoice within 30 days after order completion. Electronic invoices are usually sent to the mailbox within 24 hours."
            },
            @{
                name = "metrics-$RunId.txt"
                text = "Operations metrics. The platform should track issue hit rate, answer adoption rate, human handoff rate, average response time, and customer satisfaction."
            },
            @{
                name = "support-$RunId.txt"
                text = "Support escalation. If the self-service bot fails to solve the same user question twice, the system should create a support ticket, attach the conversation context, and hand the case to a human agent."
            },
            @{
                name = "shipping-$RunId.txt"
                text = "Shipping tracking. Users can view shipment location after the carrier scans the parcel. If tracking is not updated for 48 hours, customer service should contact the carrier and notify the user."
            },
            @{
                name = "distractor-$RunId.txt"
                text = "Distractor document. Food packaging, invoice title, and response time may appear together here, but this document does not provide complete return, invoice, or operations rules."
            }
        )

        foreach ($doc in $documents) {
            $upload = Upload-TextDocument -BackendUrl $backendUrl -FileName $doc.name -Content $doc.text
            $documentIds.Add([string]$upload.document_id) | Out-Null
        }

        $cases = @(
            @{
                id = "return-food"
                question = "Do food items support seven-day no-reason returns?"
                expected_doc = "returns"
                expected_terms = @("Food", "do not support", "seven-day")
            },
            @{
                id = "return-synonym"
                question = "Can opened personal-care items be returned without reason within seven days?"
                expected_doc = "returns"
                expected_terms = @("opened personal-care", "do not support", "seven-day")
            },
            @{
                id = "invoice-time"
                question = "How soon are electronic invoices usually sent?"
                expected_doc = "invoice"
                expected_terms = @("electronic invoice", "24 hours", "mailbox")
            },
            @{
                id = "quality-metrics"
                question = "Which operations metrics should the platform track?"
                expected_doc = "metrics"
                expected_terms = @("hit rate", "adoption rate", "handoff")
            },
            @{
                id = "distractor-resistance"
                question = "Food packaging appears in one note, but what is the actual rule for food returns?"
                expected_doc = "returns"
                expected_terms = @("Food", "do not support", "returns")
            },
            @{
                id = "handoff-next-step"
                question = "If the bot fails to solve the same user question twice, what should happen next?"
                expected_doc = "support"
                expected_terms = @("support ticket", "conversation context", "human agent")
            },
            @{
                id = "tracking-stale"
                question = "What should customer service do when parcel tracking is not updated for 48 hours?"
                expected_doc = "shipping"
                expected_terms = @("48 hours", "contact the carrier", "notify the user")
            },
            @{
                id = "unknown-vip-discount"
                question = "What is the VIP discount rate for platinum members?"
                expected_doc = ""
                expected_terms = @()
                expected_no_answer = $true
            }
        )

        $rows = @()
        foreach ($case in $cases) {
            $events = Ask-Question -BackendUrl $backendUrl -Question $case.question
            $answer = ((@($events | Where-Object { $_.type -eq "chunk" }) | ForEach-Object { [string]$_.data }) -join "").Trim()
            $trace = @($events | Where-Object { $_.type -eq "trace" })[-1].data
            $evaluation = @($events | Where-Object { $_.type -eq "evaluation" })[-1].data
            $rankStep = Get-Step -Trace $trace -Name "rank"
            $citeStep = Get-Step -Trace $trace -Name "cite"
            $topDocument = ""
            if (@($rankStep.results).Count -gt 0) {
                $topDocument = [string]$rankStep.results[0].document_name
            }
            $citationDocs = @($citeStep.results | ForEach-Object { [string]$_.document_name })
            $citationCount = @($citationDocs).Count
            $expectedDoc = [string]$case.expected_doc
            $expectedNoAnswer = [bool]$case.expected_no_answer
            $isAnswerCase = -not $expectedNoAnswer
            $top1Hit = $false
            $citationHit = $false
            if ($isAnswerCase) {
                $top1Hit = $topDocument -like "$expectedDoc-*"
                $citationHit = (@($citationDocs | Where-Object { $_ -like "$expectedDoc-*" }).Count -gt 0)
            }
            $termHits = @($case.expected_terms | Where-Object { Test-ContainsTerm -Text $answer -Term ([string]$_) }).Count
            $noAnswerPass = $false
            if ($expectedNoAnswer) {
                $noAnswerPass = Test-NoAnswerPass -Answer $answer -CitationCount $citationCount
            }
            $behaviorPass = if ($expectedNoAnswer) {
                $noAnswerPass
            } else {
                $citationHit -and ($termHits -gt 0)
            }

            $rows += [ordered]@{
                id = $case.id
                question = $case.question
                expected_doc = $expectedDoc
                expected_no_answer = $expectedNoAnswer
                top_document = $topDocument
                top1_hit = $top1Hit
                citation_hit = $citationHit
                citation_count = $citationCount
                expected_term_hits = $termHits
                expected_term_count = @($case.expected_terms).Count
                no_answer_pass = $noAnswerPass
                behavior_pass = $behaviorPass
                llm_success = if ($UseRealLlm) { Test-LlmSuccess -Answer $answer } else { $false }
                quality_score = $evaluation.overall_score
                quality_label = $evaluation.label
                answer = $answer
            }
        }

        $answerRows = @($rows | Where-Object { -not $_.expected_no_answer })
        $noAnswerRows = @($rows | Where-Object { $_.expected_no_answer })
        $top1Hits = @($answerRows | Where-Object { $_.top1_hit }).Count
        $citationHits = @($answerRows | Where-Object { $_.citation_hit }).Count
        $noAnswerHits = @($noAnswerRows | Where-Object { $_.no_answer_pass }).Count
        $behaviorHits = @($rows | Where-Object { $_.behavior_pass }).Count
        $llmSuccesses = @($rows | Where-Object { $_.llm_success }).Count
        $avgQuality = 0.0
        if ($rows.Count -gt 0) {
            $avgQuality = (($rows | ForEach-Object {
                if ($null -eq $_.quality_score) { 0.0 } else { [double]$_.quality_score }
            } | Measure-Object -Average).Average)
        }

        return [ordered]@{
            mode = $ModeName
            reranker_enabled = $RerankerEnabled
            top1_hits = $top1Hits
            citation_hits = $citationHits
            answer_case_count = $answerRows.Count
            no_answer_hits = $noAnswerHits
            no_answer_case_count = $noAnswerRows.Count
            behavior_hits = $behaviorHits
            llm_successes = $llmSuccesses
            case_count = $rows.Count
            average_quality = $avgQuality
            rows = $rows
        }
    } finally {
        if (-not $KeepDocuments) {
            foreach ($documentId in $documentIds) {
                try {
                    Invoke-RestMethod -Method Delete -Uri "$backendUrl/api/documents/$documentId" -TimeoutSec 10 | Out-Null
                } catch {}
            }
        }
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-PortListener -Port $BackendPort
    }
}

try {
    if (-not (Test-Path -LiteralPath $BackendPython)) {
        throw "Backend Python not found: $BackendPython"
    }
    New-Item -ItemType Directory -Path $DataDir -Force | Out-Null

    $baseline = Run-Suite -ModeName "baseline" -RerankerEnabled $false
    $reranked = Run-Suite -ModeName "reranker" -RerankerEnabled $true
    $report = [ordered]@{
        created_at = (Get-Date).ToString("s")
        llm_mode = if ($UseRealLlm) { "real" } else { "offline" }
        baseline = $baseline
        reranker = $reranked
        delta = [ordered]@{
            top1_hits = $reranked.top1_hits - $baseline.top1_hits
            citation_hits = $reranked.citation_hits - $baseline.citation_hits
            no_answer_hits = $reranked.no_answer_hits - $baseline.no_answer_hits
            behavior_hits = $reranked.behavior_hits - $baseline.behavior_hits
            llm_successes = $reranked.llm_successes - $baseline.llm_successes
            average_quality = [double]$reranked.average_quality - [double]$baseline.average_quality
        }
    }
    $report | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $ReportPath -Encoding UTF8

    Write-Host ""
    Write-Host "RAG evaluation suite comparison"
    Write-Host "  LLM mode:       $(if ($UseRealLlm) { "real" } else { "offline" })"
    Write-Host "  Baseline Top1:  $($baseline.top1_hits)/$($baseline.answer_case_count), Citations: $($baseline.citation_hits)/$($baseline.answer_case_count), No-answer: $($baseline.no_answer_hits)/$($baseline.no_answer_case_count), Behavior: $($baseline.behavior_hits)/$($baseline.case_count), LLM: $($baseline.llm_successes)/$($baseline.case_count), Quality: $([math]::Round([double]$baseline.average_quality * 100))%"
    Write-Host "  Reranker Top1:  $($reranked.top1_hits)/$($reranked.answer_case_count), Citations: $($reranked.citation_hits)/$($reranked.answer_case_count), No-answer: $($reranked.no_answer_hits)/$($reranked.no_answer_case_count), Behavior: $($reranked.behavior_hits)/$($reranked.case_count), LLM: $($reranked.llm_successes)/$($reranked.case_count), Quality: $([math]::Round([double]$reranked.average_quality * 100))%"
    Write-Host "  Report: $ReportPath"
} finally {
    foreach ($key in $originalEnv.Keys) {
        if ($null -eq $originalEnv[$key]) {
            Remove-Item -LiteralPath "Env:\$key" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:\$key" -Value $originalEnv[$key]
        }
    }
}
