$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ourtime-config.ps1')
$projectRoot = $PSScriptRoot
$dataRoot = [IO.Path]::GetFullPath($env:PHOTO_LIBRARY_DATA)
$port = [int]$env:PHOTO_LIBRARY_PORT
$serverUrl = "http://127.0.0.1:$port"
$normalizedData = [IO.Path]::GetFullPath($dataRoot).ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $dataKey = ([BitConverter]::ToString(
        $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalizedData))
    )).Replace('-', '').ToLowerInvariant()
} finally {
    $sha.Dispose()
}

Assert-OurTimeWritable $dataRoot

function Test-OurTimePage {
    try {
        if (-not (Test-OurTimeListening $port)) { return $false }
        $health = Invoke-OurTimeApi "$serverUrl/api/health" 'GET' 2
        return $health.ok -and ([string]$health.data_key -eq $dataKey)
    } catch {
        return $false
    }
}

function Test-OurTimeOwnedPort {
    $appPath = Join-Path $projectRoot 'app.py'
    $connections = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($connection in $connections) {
        $listener = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)" -ErrorAction SilentlyContinue
        if ($listener -and $listener.CommandLine -and $listener.CommandLine.Contains($appPath)) {
            return $true
        }
    }
    return $false
}

function Open-OurTimePage {
    $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Start-Process "$serverUrl/?start=$stamp"
}

try {
    if (Test-OurTimePage) {
        if (-not (Test-OurTimeOwnedPort)) { throw "端口 $port 已被其他程序占用，拾光没有改用别的资料库。" }
        if ($env:PHOTO_NO_BROWSER -ne '1') { Open-OurTimePage }
        exit 0
    }
} catch {
    if ($_.Exception.Message -like '*已被其他程序占用*') { throw }
}

$pythonExe = Get-OurTimePython
if (-not $pythonExe) {
    throw "未找到 Python。请在本目录创建 .venv 并安装 requirements.txt，或在 config.local.json 指定 python_exe。说明见 docs/SETUP.md"
}

$env:NO_ALBUMENTATIONS_UPDATE = '1'
$env:PHOTO_LIBRARY_PORT = [string]$port
$appPath = Join-Path $projectRoot 'app.py'
$stdoutLog = Join-Path $dataRoot 'server.log'
$stderrLog = Join-Path $dataRoot 'server-error.log'
$pidFile = Join-Path $dataRoot 'server.pid'
if (-not (Test-OurTimeListening $port)) {
    if (Test-Path -LiteralPath $pidFile) {
        $staleId = 0
        [void][int]::TryParse((Get-Content -LiteralPath $pidFile -ErrorAction SilentlyContinue), [ref]$staleId)
        if ($staleId -gt 0) { Stop-OurTimeAppTree $staleId $projectRoot }
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    }
    Get-OurTimeAppProcesses $projectRoot | ForEach-Object {
        Stop-OurTimeAppTree ([int]$_.ProcessId) $projectRoot
    }
}
$process = Start-Process -FilePath $pythonExe -ArgumentList @(
    '"' + $appPath + '"', '--port', "$port"
) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru

Write-Output "Waiting for $serverUrl ..."
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-OurTimePage) {
        $health = Invoke-OurTimeApi "$serverUrl/api/health" 'GET' 2
        ([int]$health.pid) | Set-Content -LiteralPath (Join-Path $dataRoot 'server.pid') -Encoding ASCII
        if ($env:PHOTO_NO_BROWSER -ne '1') { Open-OurTimePage }
        exit 0
    }
    if ($process.HasExited) { break }
}
throw "启动失败，请查看 $stderrLog"
