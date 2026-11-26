$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw '项目 Python 虚拟环境不存在。' }
Set-Location -LiteralPath $projectRoot
& $python (Join-Path $PSScriptRoot 'yosaki_runtime.py') test
if ($LASTEXITCODE -ne 0) { throw 'Test 启动失败；请检查上方路径提示。' }
