$script:OurTimeRoot = $PSScriptRoot

function Get-OurTimeSettings {
    $settings = @{}
    foreach ($name in @('config.json', 'config.local.json')) {
        $path = Join-Path $script:OurTimeRoot $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        try {
            $parsed = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        } catch {
            continue
        }
        if (-not $parsed) { continue }
        $parsed.PSObject.Properties | ForEach-Object {
            if ($null -ne $_.Value -and "$($_.Value)" -ne '') {
                $settings[$_.Name] = $_.Value
            }
        }
    }
    return $settings
}

function Resolve-OurTimePath([string]$value, [string]$defaultRelative) {
    $raw = if ($value) { $value } else { $defaultRelative }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $raw = $defaultRelative
    }
    if ([IO.Path]::IsPathRooted($raw)) {
        return [IO.Path]::GetFullPath($raw)
    }
    return [IO.Path]::GetFullPath((Join-Path $script:OurTimeRoot $raw))
}

$script:OurTimeSettings = Get-OurTimeSettings

if (-not $env:PHOTO_LIBRARY_DATA) {
    $env:PHOTO_LIBRARY_DATA = Resolve-OurTimePath $script:OurTimeSettings['data_dir'] 'data'
}
if (-not $env:PHOTO_LIBRARY_PORT) {
    $env:PHOTO_LIBRARY_PORT = '8765'
}
if (-not $env:PHOTO_MODEL_ROOT -and $script:OurTimeSettings['model_root']) {
    $env:PHOTO_MODEL_ROOT = Resolve-OurTimePath $script:OurTimeSettings['model_root'] 'resources/models'
}
if (-not $env:PHOTO_GEO_ROOT -and $script:OurTimeSettings['geo_root']) {
    $env:PHOTO_GEO_ROOT = Resolve-OurTimePath $script:OurTimeSettings['geo_root'] 'resources/geo'
}
if (-not $env:PHOTO_FACE_LABEL_DIR -and $script:OurTimeSettings['face_label_dir']) {
    $env:PHOTO_FACE_LABEL_DIR = Resolve-OurTimePath $script:OurTimeSettings['face_label_dir'] 'web/assets/face-labels'
}
if (-not $env:PHOTO_FOLDER_ONLY_DRIVE -and $script:OurTimeSettings.ContainsKey('folder_only_drive')) {
    $env:PHOTO_FOLDER_ONLY_DRIVE = [string]$script:OurTimeSettings['folder_only_drive']
}
if (-not $env:PHOTO_OBJECTS_ENABLED -and $script:OurTimeSettings.ContainsKey('objects_enabled')) {
    $env:PHOTO_OBJECTS_ENABLED = if ($script:OurTimeSettings['objects_enabled']) { '1' } else { '0' }
}
if (-not $env:PHOTO_CHROMIUM -and $script:OurTimeSettings['chromium_path']) {
    $env:PHOTO_CHROMIUM = [string]$script:OurTimeSettings['chromium_path']
}

function Get-OurTimePython {
    if ($env:PHOTO_PYTHON -and (Test-Path -LiteralPath $env:PHOTO_PYTHON)) {
        return $env:PHOTO_PYTHON
    }
    $configured = $script:OurTimeSettings['python_exe']
    if ($configured) {
        $resolved = Resolve-OurTimePath $configured '.venv/Scripts/python.exe'
        if (Test-Path -LiteralPath $resolved) { return $resolved }
    }
    $venvPython = Join-Path $script:OurTimeRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython) { return $venvPython }
    return $null
}

function Assert-OurTimeWritable([string]$dataRoot) {
    try {
        New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
        $probe = Join-Path $dataRoot '.ourtime-write-check'
        Set-Content -LiteralPath $probe -Value 'ok' -Encoding ASCII
        Remove-Item -LiteralPath $probe -Force
    } catch {
        throw "资料库目录无法写入：$dataRoot`n请把拾光放到可写位置，或在 config.local.json 里指定 data_dir。"
    }
}

function Test-OurTimeListening([int]$port) {
    try {
        $client = [Net.Sockets.TcpClient]::new()
        $client.Connect('127.0.0.1', $port)
        $ok = $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

function Invoke-OurTimeApi([string]$uri, [string]$method = 'GET', [int]$timeoutSec = 5) {
    $request = [Net.HttpWebRequest]::Create($uri)
    $request.Proxy = $null
    $request.Method = $method
    $request.Timeout = [Math]::Max(1000, $timeoutSec * 1000)
    $request.ReadWriteTimeout = $request.Timeout
    $request.ContentLength = 0
    $request.KeepAlive = $false
    $response = $null
    try {
        $response = $request.GetResponse()
        $stream = $response.GetResponseStream()
        $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        $reader.Close()
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    } finally {
        if ($response) { $response.Close() }
    }
}

function Get-OurTimeAppProcesses([string]$projectRoot) {
    $appPath = Join-Path $projectRoot 'app.py'
    @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue) |
        Where-Object { $_.CommandLine -and $_.CommandLine.IndexOf($appPath, [StringComparison]::OrdinalIgnoreCase) -ge 0 }
}

function Stop-OurTimeAppTree([int]$processId, [string]$projectRoot) {
    $appPath = Join-Path $projectRoot 'app.py'
    $target = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
    $ids = @()
    if ($target) { $ids += [int]$target.ProcessId }
    if ($target -and $target.ParentProcessId) {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($target.ParentProcessId)" -ErrorAction SilentlyContinue
        if ($parent -and $parent.CommandLine -and $parent.CommandLine.IndexOf($appPath, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $ids += [int]$parent.ProcessId
        }
    }
    Get-OurTimeAppProcesses $projectRoot | ForEach-Object {
        if ([int]$_.ParentProcessId -eq $processId) { $ids += [int]$_.ProcessId }
    }
    $ids | Select-Object -Unique | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
}
