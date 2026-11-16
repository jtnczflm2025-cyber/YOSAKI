$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$vite = Join-Path $projectRoot 'node_modules\.bin\vite.cmd'
if (-not (Test-Path -LiteralPath $vite)) { throw '前端依赖不存在，请先安装项目依赖。' }
$previousProxy = [Environment]::GetEnvironmentVariable('YOSAKI_API_PROXY', 'Process')
try {
    $env:YOSAKI_API_PROXY = 'http://127.0.0.1:8001'
    Set-Location -LiteralPath (Join-Path $projectRoot 'frontend')
    & $vite --host 127.0.0.1 --port 5174 --strictPort
} finally {
    if ($null -eq $previousProxy) {
        Remove-Item Env:YOSAKI_API_PROXY -ErrorAction SilentlyContinue
    } else {
        $env:YOSAKI_API_PROXY = $previousProxy
    }
}
