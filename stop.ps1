$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'ourtime-config.ps1')
$dataRoot = [IO.Path]::GetFullPath($env:PHOTO_LIBRARY_DATA)
$port = [int]$env:PHOTO_LIBRARY_PORT
$serverUrl = "http://127.0.0.1:$port"
$pidFile = Join-Path $dataRoot 'server.pid'
$normalizedData = [IO.Path]::GetFullPath($dataRoot).ToLowerInvariant()
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $dataKey = ([BitConverter]::ToString(
        $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($normalizedData))
    )).Replace('-', '').ToLowerInvariant()
} finally {
    $sha.Dispose()
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output 'No recorded background process for this data directory.'
    exit
}

$serverProcessId = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$serverProcessId"
$appPath = Join-Path $PSScriptRoot 'app.py'
if ($process -and $process.CommandLine.Contains($appPath)) {
    if (-not (Test-OurTimeListening $port)) {
        Stop-OurTimeAppTree $serverProcessId $PSScriptRoot
        Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
        Write-Output 'OurTime had already stopped listening; the leftover process was cleared.'
        exit 0
    }
    try {
        $health = Invoke-OurTimeApi "$serverUrl/api/health" 'GET' 3
        if ([string]$health.data_key -ne $dataKey -or [int]$health.pid -ne $serverProcessId) {
            throw 'The server on this port does not own the configured data directory.'
        }
        Invoke-OurTimeApi "$serverUrl/api/scan/pause" 'POST' 3 | Out-Null
        for ($i = 0; $i -lt 60; $i++) {
            $state = Invoke-OurTimeApi "$serverUrl/api/status" 'GET' 3
            if (-not $state.job -or $state.job.status -notin @('running', 'pausing')) { break }
            Start-Sleep -Milliseconds 500
        }
        if ($state.job -and $state.job.status -in @('running', 'pausing')) {
            throw 'The current file has not reached a safe stop point.'
        }
        Invoke-OurTimeApi "$serverUrl/api/shutdown" 'POST' 3 | Out-Null
        for ($i = 0; $i -lt 40; $i++) {
            if (-not (Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue)) { break }
            Start-Sleep -Milliseconds 250
        }
        if (Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue) {
            if (-not (Test-OurTimeListening $port)) {
                Stop-OurTimeAppTree $serverProcessId $PSScriptRoot
            } else {
                throw 'The server did not stop safely; it was not force-terminated.'
            }
        }
    } catch {
        throw "OurTime did not stop safely: $_"
    }
    Remove-Item -LiteralPath $pidFile
    Write-Output 'OurTime stopped safely.'
} elseif (-not $process) {
    Remove-Item -LiteralPath $pidFile
    Write-Output 'The server was already stopped; the stale PID was removed.'
} else {
    throw 'The PID belongs to another process and was not stopped.'
}
