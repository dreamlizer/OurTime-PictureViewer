@echo off
setlocal
chcp 65001 >nul
rem Build dist green package then run tools\packaging\smoke_packed.py.
rem Set PHOTO_PACK_SKIP_SMOKE=1 to build only. Extra args go to tools\pack_green.py.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\packaging\build-green.ps1" %*
exit /b %ERRORLEVEL%
