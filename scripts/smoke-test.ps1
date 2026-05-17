[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8001,
    [int]$TimeoutSeconds = 10,
    [int]$RagTimeoutSeconds = 90,
    [switch]$SkipUpload
)

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$BackendUrl = "http://${HostName}:${BackendPort}"
$FrontendUrl = "http://${HostName}:${FrontendPort}"

function Write-Step {
    param([string]$Message)
    Write-Host "[smoke] $Message"
}

function Invoke-CheckedWebRequest {
    param(
        [string]$Name,
        [string]$Url
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec $TimeoutSeconds
    } catch {
        throw "$Name failed at $Url. $($_.Exception.Message)"
    }

    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "$Name returned HTTP $($response.StatusCode) at $Url"
    }
    Write-Step "$Name OK: HTTP $($response.StatusCode)"
    return $response
}

function Invoke-CheckedJson {
    param(
        [string]$Name,
        [string]$Url
    )

    $response = Invoke-CheckedWebRequest -Name $Name -Url $Url
    try {
        return $response.Content | ConvertFrom-Json
    } catch {
        throw "$Name did not return valid JSON"
    }
}

function Invoke-CheckedJsonPost {
    param(
        [string]$Name,
        [string]$Url,
        [object]$Body,
        [int]$TimeoutSec = $TimeoutSeconds
    )

    $jsonBody = $Body | ConvertTo-Json -Depth 10 -Compress
    try {
        $response = Invoke-WebRequest `
            -UseBasicParsing `
            -Method Post `
            -ContentType "application/json" `
            -Body $jsonBody `
            -Uri $Url `
            -TimeoutSec $TimeoutSec
    } catch {
        throw "$Name failed at $Url. $($_.Exception.Message)"
    }

    if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
        throw "$Name returned HTTP $($response.StatusCode) at $Url"
    }

    try {
        $parsed = $response.Content | ConvertFrom-Json
    } catch {
        throw "$Name did not return valid JSON"
    }

    Write-Step "$Name OK: HTTP $($response.StatusCode)"
    return $parsed
}

function Assert-Score {
    param(
        [string]$Name,
        [object]$Value
    )

    if ($null -eq $Value) {
        throw "$Name was missing from evaluation response"
    }
    $number = [double]$Value
    if ($number -lt 0 -or $number -gt 1) {
        throw "$Name was outside the expected 0..1 range: $number"
    }
}

Write-Step "Checking backend health"
$health = Invoke-CheckedJson -Name "backend health" -Url "$BackendUrl/api/health"
if ($health.status -ne "ok") {
    throw "Backend health status is not ok: $($health | ConvertTo-Json -Compress)"
}

Write-Step "Checking knowledge document listing"
$documentsResponse = Invoke-CheckedJson -Name "knowledge documents" -Url "$BackendUrl/api/kb/documents"
$documents = @($documentsResponse.documents)
Write-Step "Knowledge documents visible: $($documents.Count)"

if ($documents.Count -gt 0) {
    $firstDocumentId = $documents[0].document_id
    $chunksResponse = Invoke-CheckedJson -Name "document chunks" -Url "$BackendUrl/api/documents/$firstDocumentId/chunks"
    $chunks = @($chunksResponse.chunks)
    Write-Step "First document chunks visible: $($chunks.Count)"
}

Write-Step "Checking frontend HTML"
$frontendResponse = Invoke-CheckedWebRequest -Name "frontend" -Url $FrontendUrl
$cssMatches = [regex]::Matches($frontendResponse.Content, 'href="([^"]*\.css[^"]*)"')
if ($cssMatches.Count -eq 0) {
    throw "Frontend HTML did not include a CSS asset. The page may render without styles."
}

$cssHref = $cssMatches[0].Groups[1].Value
if ($cssHref.StartsWith("/")) {
    $cssUrl = "$FrontendUrl$cssHref"
} else {
    $cssUrl = $cssHref
}
try {
    Invoke-CheckedWebRequest -Name "frontend css" -Url $cssUrl | Out-Null
} catch {
    Write-Step "Frontend CSS reference was present but could not be fetched directly in this server mode: $cssUrl"
}

if (-not $SkipUpload) {
    Write-Step "Checking upload, RAG chat, evaluation, and delete round trip"
    $tempName = ".smoke-test-" + ([guid]::NewGuid().ToString("N")) + ".txt"
    $tempPath = Join-Path $Root $tempName
    $documentId = $null

    try {
        Set-Content -LiteralPath $tempPath -Value @"
Smoke test document.
This temporary document verifies upload, indexing, listing, chunk preview, and delete.
"@ -Encoding UTF8

        $uploadRaw = & curl.exe -s --max-time 30 -X POST -F "file=@$tempPath;type=text/plain" "$BackendUrl/api/documents/upload"
        if ($LASTEXITCODE -ne 0) {
            throw "curl upload failed with exit code $LASTEXITCODE"
        }
        $upload = $uploadRaw | ConvertFrom-Json
        if ($upload.status -eq "error") {
            throw "Upload returned error: $uploadRaw"
        }
        $documentId = $upload.document_id
        if (-not $documentId) {
            throw "Upload response did not include document_id: $uploadRaw"
        }
        Write-Step "Upload OK: $documentId"

        $chunksResponse = Invoke-CheckedJson -Name "uploaded document chunks" -Url "$BackendUrl/api/documents/$documentId/chunks"
        if (@($chunksResponse.chunks).Count -lt 1) {
            throw "Uploaded document has no visible chunks"
        }
        Write-Step "Uploaded document chunks visible: $(@($chunksResponse.chunks).Count)"

        Write-Step "Checking KB chat stream"
        $chatBody = @{
            query = "What does the smoke test document verify?"
            mode = "kb"
            top_k = 10
        } | ConvertTo-Json -Compress

        try {
            $chatResponse = Invoke-WebRequest `
                -UseBasicParsing `
                -Method Post `
                -ContentType "application/json" `
                -Body $chatBody `
                -Uri "$BackendUrl/api/chat/stream" `
                -TimeoutSec $RagTimeoutSeconds
        } catch {
            throw "KB chat stream failed. $($_.Exception.Message)"
        }

        if ($chatResponse.StatusCode -lt 200 -or $chatResponse.StatusCode -ge 300) {
            throw "KB chat stream returned HTTP $($chatResponse.StatusCode)"
        }

        $chatContent = [string]$chatResponse.Content
        if ($chatContent -notmatch "event: chunk") {
            throw "KB chat stream did not include any chunk events"
        }
        if ($chatContent -notmatch "event: trace") {
            throw "KB chat stream did not include a trace event"
        }
        if ($chatContent -notmatch "upload|indexing|listing|chunk|delete|Smoke test") {
            throw "KB chat stream did not appear to use the uploaded smoke-test document"
        }
        Write-Step "KB chat stream OK"

        Write-Step "Checking RAG evaluation"
        $evaluation = Invoke-CheckedJsonPost `
            -Name "rag evaluation" `
            -Url "$BackendUrl/api/kb/evaluate" `
            -TimeoutSec $RagTimeoutSeconds `
            -Body @{
                query = "What does the smoke test document verify?"
                expected_answer = "upload indexing listing chunk preview delete"
            }

        if (-not $evaluation.answer) {
            throw "Evaluation response did not include an answer"
        }
        Assert-Score -Name "faithfulness" -Value $evaluation.faithfulness
        Assert-Score -Name "answer_relevancy" -Value $evaluation.answer_relevancy
        Assert-Score -Name "context_recall" -Value $evaluation.context_recall
        Assert-Score -Name "context_precision" -Value $evaluation.context_precision
        if ([double]$evaluation.latency_ms -le 0) {
            throw "Evaluation latency_ms must be positive"
        }
        Write-Step "RAG evaluation OK: faithfulness=$($evaluation.faithfulness), relevancy=$($evaluation.answer_relevancy)"

        $deleteResponse = Invoke-RestMethod -Method Delete -Uri "$BackendUrl/api/documents/$documentId" -TimeoutSec $TimeoutSeconds
        if ($deleteResponse.status -notin @("deleted", "partial")) {
            throw "Delete returned unexpected status: $($deleteResponse | ConvertTo-Json -Compress)"
        }
        Write-Step "Delete OK: $($deleteResponse.status)"
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force
        }
    }
}

Write-Host ""
Write-Host "Smoke test passed."
