param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment is missing.' }
Set-Location -LiteralPath $projectRoot

& $python (Join-Path $PSScriptRoot 'yosaki_runtime.py') development --voice-acceptance --port $Port
if ($LASTEXITCODE -ne 0) { throw 'Guarded Voice acceptance startup failed.' }
