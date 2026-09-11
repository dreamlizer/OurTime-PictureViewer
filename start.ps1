$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$port = 8765
$serverUrl = "http://127.0.0.1:$port"
try {
    $running = Invoke-RestMethod "$serverUrl/api/status" -TimeoutSec 2
    if ($running.capabilities.data_dir -eq $dataRoot) {
        if ($env:PHOTO_NO_BROWSER -ne '1') { Start-Process $serverUrl }
        exit 0
    }
    throw '端口 8765 已被另一个服务使用。'
} catch {
    if ($_.Exception.Message -like '*另一个服务*') { throw }
}
$pythonCandidates = @(
    (Join-Path $projectRoot '.venv\Scripts\python.exe'),
    'C:\Users\A\PycharmProjects\ImageBrowser\.venv\Scripts\python.exe'
)
$pythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExe) { throw '未找到 Python 运行环境。请查看 README.md 中的安装说明。' }
$env:NO_ALBUMENTATIONS_UPDATE = '1'
$appPath = Join-Path $projectRoot 'app.py'
$stdoutLog = Join-Path $dataRoot 'server.log'
$stderrLog = Join-Path $dataRoot 'server-error.log'
$process = Start-Process -FilePath $pythonExe -ArgumentList @('"' + $appPath + '"', '--port', "$port") -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru
$process.Id | Set-Content -LiteralPath (Join-Path $dataRoot 'server.pid') -Encoding ASCII
for ($attempt=0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $result = Invoke-RestMethod "$serverUrl/api/status" -TimeoutSec 2
        if ($result.capabilities.data_dir -eq $dataRoot) {
            if ($env:PHOTO_NO_BROWSER -ne '1') { Start-Process $serverUrl }
            exit 0
        }
    } catch {}
    if ($process.HasExited) { break }
}
throw "启动失败，请查看 $stderrLog"
