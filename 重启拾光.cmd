@echo off
setlocal
pushd "%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PHOTO_RESTART_CMD_PATH=%~f0"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart.ps1"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
  echo.
  echo [ERROR] Restart failed. See the message above and data\server-error.log.
  timeout /t 8 /nobreak >nul
)

popd
endlocal & exit /b %RESULT%
