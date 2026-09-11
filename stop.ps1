$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot 'data\server.pid'
if (-not (Test-Path -LiteralPath $pidFile)) { Write-Output '没有本项目记录的后台进程。'; exit }
$serverProcessId = [int](Get-Content -LiteralPath $pidFile)
$process = Get-CimInstance Win32_Process -Filter "ProcessId=$serverProcessId"
$appPath = Join-Path $PSScriptRoot 'app.py'
if ($process -and $process.CommandLine.Contains($appPath)) {
    try {
        Invoke-RestMethod 'http://127.0.0.1:8765/api/scan/pause' -Method Post -TimeoutSec 3 | Out-Null
        for ($i=0; $i -lt 60; $i++) {
            $state = Invoke-RestMethod 'http://127.0.0.1:8765/api/status' -TimeoutSec 3
            if ($state.job.status -notin @('running','pausing')) { break }
            Start-Sleep -Milliseconds 500
        }
        if ($state.job.status -in @('running','pausing')) { throw '当前文件尚未处理完，请稍后再停止。' }
    } catch { throw "未安全停止扫描：$_" }
    $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $serverProcessId -and $_.CommandLine -and $_.CommandLine.Contains($appPath) }
    foreach ($child in $children) {
        if (Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $child.ProcessId }
    }
    if (Get-Process -Id $serverProcessId -ErrorAction SilentlyContinue) { Stop-Process -Id $serverProcessId }
    Remove-Item -LiteralPath $pidFile
    Write-Output '拾光后台已停止。'
} elseif (-not $process) {
    Remove-Item -LiteralPath $pidFile
    Write-Output '后台已经停止。'
} else { throw '进程编号已被其他程序使用，未停止它。' }
