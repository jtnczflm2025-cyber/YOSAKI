param(
    [ValidateRange(1, 65535)]
    [int]$Port = 5173,
    [ValidateRange(1, 65535)]
    [int]$ApiPort = 8000,
    [switch]$ExperimentalWsAndBarge,
    [switch]$HeadsetBargeAcceptance
)

$ErrorActionPreference = 'Stop'
if ($HeadsetBargeAcceptance -and ($Port -eq 5173 -or $ApiPort -eq 8000)) {
    throw 'Headset acceptance requires separate frontend and backend ports.'
}
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'frontend\node_modules'))) {
    throw 'Frontend dependencies are missing.'
}
$env:YOSAKI_API_PROXY = "http://127.0.0.1:$ApiPort"
$env:VITE_YOSAKI_VOICE_AUTO_READ_ISOLATED = '1'
$env:VITE_YOSAKI_SCRIBE_DAILY = '1'
$env:VITE_YOSAKI_VOICE_CONTINUOUS_DAILY = '1'
$env:VITE_YOSAKI_VOICE_CALL_SETTINGS = '1'
$env:VITE_YOSAKI_VOICE_BARGE_IN_ISOLATED = if ($ExperimentalWsAndBarge) { '1' } else { '0' }
$env:VITE_YOSAKI_HEADSET_BARGE_ACCEPTANCE = if ($HeadsetBargeAcceptance) { '1' } else { '0' }
$env:VITE_YOSAKI_BARGE_FAKE_TEST = '0'
$env:VITE_YOSAKI_SCRIBE_FAKE_TEST = '0'
$env:VITE_YOSAKI_SCRIBE_DRAFT_ISOLATED = '0'
$env:VITE_YOSAKI_CONTINUOUS_FAKE_CAPTURE = '0'
$env:VITE_YOSAKI_CONTINUOUS_FAKE_CAPTURE_ONCE = '0'
$env:VITE_YOSAKI_VOICE_CONTINUOUS_ISOLATED = '0'
$env:VITE_YOSAKI_V4_OUTPUT_ISOLATED = '0'
$env:VITE_YOSAKI_V4_PRODUCT_OUTPUT_ISOLATED = '0'
$env:VITE_YOSAKI_V4_DYNAMIC_CHAT_ISOLATED = '0'
$env:VITE_YOSAKI_V4_DYNAMIC_CANDIDATE_ISOLATED = '0'
Set-Location -LiteralPath $projectRoot
& npm --prefix frontend run dev -- --host 127.0.0.1 --port $Port --strictPort
if ($LASTEXITCODE -ne 0) { throw 'Daily Voice frontend startup failed.' }
