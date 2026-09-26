$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))

function Find-PackPython {
    if ($env:PHOTO_PYTHON -and (Test-Path -LiteralPath $env:PHOTO_PYTHON)) {
        return [IO.Path]::GetFullPath($env:PHOTO_PYTHON)
    }
    $venv = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venv) {
        return $venv
    }
    $localConfig = Join-Path $projectRoot 'config.local.json'
    if (Test-Path -LiteralPath $localConfig) {
        try {
            $configured = (Get-Content -LiteralPath $localConfig -Raw -Encoding UTF8 | ConvertFrom-Json).python_exe
            if ($configured -and (Test-Path -LiteralPath $configured)) {
                return [IO.Path]::GetFullPath($configured)
            }
        } catch {
            Write-Warning 'Could not read python_exe from config.local.json; continuing Python 3.12 discovery.'
        }
    }
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        $found = & $launcher.Source -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $found -and (Test-Path -LiteralPath $found)) {
            return [IO.Path]::GetFullPath($found)
        }
    }
    $installed = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path -LiteralPath $installed) {
        return $installed
    }
    return $null
}

function Find-PackageZip {
    $distDir = Join-Path $projectRoot 'dist'
    if (-not (Test-Path -LiteralPath $distDir)) {
        return $null
    }
    $zips = @(Get-ChildItem -LiteralPath $distDir -Filter '*.zip' -File -ErrorAction SilentlyContinue)
    if ($zips.Count -eq 0) {
        return $null
    }
    return ($zips | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}

$pythonExe = Find-PackPython
if (-not $pythonExe) {
    throw 'Python 3.12 was not found. Set PHOTO_PYTHON or install dependencies in the project .venv.'
}

# Keep this script ASCII-only: Windows PowerShell 5.1 reads .ps1 without BOM as ANSI.
# Layout: launcher exes + App/ (program) + Data/ (empty user library).
$env:PYTHONUTF8 = '1'
$env:NO_ALBUMENTATIONS_UPDATE = '1'

Write-Output '[1/2] Building the green package (App + empty Data)...'
& $pythonExe (Join-Path $projectRoot 'tools\pack_green.py') @args
if ($LASTEXITCODE -ne 0) {
    throw "Packaging failed with exit code $LASTEXITCODE."
}

$zipPath = Find-PackageZip
if (-not $zipPath) {
    throw "Expected package zip was not created under dist\."
}

if ($env:PHOTO_PACK_SKIP_SMOKE -eq '1') {
    Write-Output '[2/2] Skipping acceptance (PHOTO_PACK_SKIP_SMOKE=1).'
    Write-Output "Package: $zipPath"
    exit 0
}

Write-Output '[2/2] Running green-package acceptance (fresh extract, isolated Data)...'
& $pythonExe (Join-Path $projectRoot 'tools\packaging\smoke_packed.py') $zipPath
if ($LASTEXITCODE -ne 0) {
    throw "Green-package acceptance failed with exit code $LASTEXITCODE."
}
Write-Output 'GREEN_PACKAGE_OK'
Write-Output "Package: $zipPath"
