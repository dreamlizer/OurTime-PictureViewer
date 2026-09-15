$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$port = if ($env:PHOTO_LIBRARY_PORT) { [int]$env:PHOTO_LIBRARY_PORT } else { 8765 }
$serverUrl = "http://127.0.0.1:$port"
$mutex = [Threading.Mutex]::new($false, 'Local\OurTimePictureViewerRestart')
$ownsMutex = $false
$exitCode = 0

try {
    # Close only older console windows launched from this exact restart command.
    try {
        $currentShell = Get-CimInstance Win32_Process -Filter "ProcessId = $PID"
        $currentCmdPid = [int]$currentShell.ParentProcessId
        $restartCmdPath = $env:PHOTO_RESTART_CMD_PATH
        if ($restartCmdPath) {
            Get-CimInstance Win32_Process -Filter "Name = 'cmd.exe'" |
                Where-Object {
                    $_.ProcessId -ne $currentCmdPid -and
                    $_.CommandLine -and
                    $_.CommandLine.IndexOf(
                        $restartCmdPath,
                        [StringComparison]::OrdinalIgnoreCase
                    ) -ge 0
                } |
                ForEach-Object {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                }
        }
    } catch {
        Write-Warning 'Could not close an older restart window; single-instance protection remains active.'
    }

    try {
        $ownsMutex = $mutex.WaitOne(0)
    } catch [Threading.AbandonedMutexException] {
        $ownsMutex = $true
    }
    if (-not $ownsMutex) {
        Write-Output 'Another OurTime restart is already running; this window will close.'
        return
    }

    $env:PHOTO_NO_BROWSER = '1'
    Write-Output '[1/2] Stopping OurTime safely...'
    & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'stop.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'The stop step failed.'
    }

    Write-Output '[2/2] Starting OurTime...'
    & $powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'start.ps1')
    if ($LASTEXITCODE -ne 0) {
        throw 'The start step failed.'
    }

    $health = Invoke-RestMethod -Uri "$serverUrl/api/health" -TimeoutSec 5
    if (-not $health.ok -or -not $health.owner) {
        throw 'The restarted service did not confirm ownership.'
    }
    Write-Output "[OK] OurTime is running at $serverUrl"
    $stamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Start-Process "$serverUrl/?restart=$stamp"
} catch {
    $exitCode = 1
    Write-Error $_
} finally {
    if ($ownsMutex) {
        $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}

exit $exitCode
