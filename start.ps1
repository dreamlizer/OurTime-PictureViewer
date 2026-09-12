$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$dataRoot = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$port = 8765
$serverUrl = "http://127.0.0.1:$port"
function Test-OurTimePage {
    try {
        $response = Invoke-WebRequest -Uri "$serverUrl/" -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}
function Test-OurTimeOwnedPort {
    $appPath = Join-Path $projectRoot 'app.py'
    $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $listener = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
        if ($listener -and $listener.CommandLine -and $listener.CommandLine.Contains($appPath)) { return $true }
    }
    return $false
}
try {
    if (Test-OurTimePage) {
        if (-not (Test-OurTimeOwnedPort)) { throw 'port 8765 is already used by another service.' }
        if ($env:PHOTO_NO_BROWSER -ne '1') { Start-Process $serverUrl }
        exit 0
    }
} catch {
    if ($_.Exception.Message -like '*already used*') { throw }
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
Write-Output "Waiting for $serverUrl ..."
for ($attempt=0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-OurTimePage) {
        if ($env:PHOTO_NO_BROWSER -ne '1') { Start-Process $serverUrl }
        exit 0
    }
    if ($process.HasExited) { break }
}
throw "启动失败，请查看 $stderrLog"
