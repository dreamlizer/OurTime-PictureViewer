$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$dataRoot = if ($env:PHOTO_LIBRARY_DATA) {
    [IO.Path]::GetFullPath($env:PHOTO_LIBRARY_DATA)
} else {
    Join-Path $projectRoot 'data'
}
$port = if ($env:PHOTO_LIBRARY_PORT) { [int]$env:PHOTO_LIBRARY_PORT } else { 8765 }
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

New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null

function Test-OurTimePage {
    try {
        $health = Invoke-RestMethod -Uri "$serverUrl/api/health" -TimeoutSec 2
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

try {
    if (Test-OurTimePage) {
        if (-not (Test-OurTimeOwnedPort)) { throw "port $port is already used by another service." }
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
if (-not $pythonExe) { throw 'Python runtime was not found. See README.md.' }

$env:NO_ALBUMENTATIONS_UPDATE = '1'
$env:PHOTO_LIBRARY_PORT = [string]$port
$appPath = Join-Path $projectRoot 'app.py'
$stdoutLog = Join-Path $dataRoot 'server.log'
$stderrLog = Join-Path $dataRoot 'server-error.log'
$process = Start-Process -FilePath $pythonExe -ArgumentList @(
    '"' + $appPath + '"', '--port', "$port"
) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru

Write-Output "Waiting for $serverUrl ..."
for ($attempt = 0; $attempt -lt 45; $attempt++) {
    Start-Sleep -Milliseconds 500
    if (Test-OurTimePage) {
        $health = Invoke-RestMethod -Uri "$serverUrl/api/health" -TimeoutSec 2
        ([int]$health.pid) | Set-Content -LiteralPath (Join-Path $dataRoot 'server.pid') -Encoding ASCII
        if ($env:PHOTO_NO_BROWSER -ne '1') { Start-Process $serverUrl }
        exit 0
    }
    if ($process.HasExited) { break }
}
throw "Startup failed. See $stderrLog"
