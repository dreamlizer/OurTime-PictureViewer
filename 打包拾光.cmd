@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\packaging\build-green.ps1" %*
exit /b %ERRORLEVEL%
