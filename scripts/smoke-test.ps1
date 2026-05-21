[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$FrontendPort = 3000,
    [int]$BackendPort = 8001,
    [int]$TimeoutSeconds = 10,
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

function Wait-DocumentReady {
    param([string]$DocumentId)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastStatus = "unknown"
    while ((Get-Date) -lt $deadline) {
        try {
            $document = Invoke-RestMethod -Method Get -Uri "$BackendUrl/api/documents/$DocumentId/status" -TimeoutSec $TimeoutSeconds
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
Invoke-CheckedWebRequest -Name "frontend css" -Url $cssUrl | Out-Null

if (-not $SkipUpload) {
    Write-Step "Checking upload and delete round trip"
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
        Write-Step "Upload accepted: $documentId"

        $readyDocument = Wait-DocumentReady -DocumentId $documentId
        if ($readyDocument.chunk_count -lt 1) {
            throw "Uploaded document became $($readyDocument.status) but has no chunks"
        }
        Write-Step "Ingestion OK: $($readyDocument.status)"

        $chunksResponse = Invoke-CheckedJson -Name "uploaded document chunks" -Url "$BackendUrl/api/documents/$documentId/chunks"
        if (@($chunksResponse.chunks).Count -lt 1) {
            throw "Uploaded document has no visible chunks"
        }

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
