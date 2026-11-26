$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$observationDir = Join-Path $projectRoot 'data\phase65-observation'
$database = Join-Path $observationDir 'yosaki.sqlite3'
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $database)) { throw '请先运行 prepare_phase65_observation.py 创建隔离副本。' }
if (-not (Test-Path -LiteralPath $python)) { throw '项目 .venv 尚未安装。' }
$env:YOSAKI_DATA_DIR = $observationDir
$env:YOSAKI_OBSERVATION_MODE = '1'
$env:YOSAKI_CHATGPT_PLAN_ENABLED = '1'
Set-Location -LiteralPath $projectRoot
& $python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
