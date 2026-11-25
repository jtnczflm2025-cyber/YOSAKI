param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [string]$PythonPath = '',
    [switch]$EnableTtdWs,
    [switch]$EnableAutoExpression,
    [switch]$EnableAiPlanning
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath).Path } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python environment is missing.' }
# A checked UTC date is persistent user configuration, never today's date by default.
# An expired date stays expired; this launcher does not approve prices or spending.
$checkedMemoryPriceDate = [Environment]::GetEnvironmentVariable('YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE', 'User')
if ($null -ne $checkedMemoryPriceDate) {
    if ($checkedMemoryPriceDate -notmatch '^\d{4}-\d{2}-\d{2}$') { throw 'Invalid stored Chronicle price verification date.' }
    $env:YOSAKI_CHRONICLE_PRICES_VERIFIED_DATE = $checkedMemoryPriceDate
}
Set-Location -LiteralPath $projectRoot
$runtimeArgs = @('development', '--voice-acceptance', '--daily-voice', '--port', $Port)
if ($EnableTtdWs) { $runtimeArgs += '--daily-ttd-ws' }
if ($EnableAiPlanning) { $runtimeArgs += '--ai-expression' }
if ($EnableAutoExpression) {
    $env:YOSAKI_V3_EXPRESSION_ENABLED = '1'
    $env:YOSAKI_VOICE_AUTO_EXPRESSION_ENABLED = '1'
}
& $python (Join-Path $PSScriptRoot 'yosaki_runtime.py') @runtimeArgs
if ($LASTEXITCODE -ne 0) { throw 'Guarded daily Voice startup failed.' }
