[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

$env:PYTHONPATH = "backend"
& "$Root\backend\venv\Scripts\python.exe" -m unittest discover -s backend\tests
