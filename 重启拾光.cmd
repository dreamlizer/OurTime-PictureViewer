@echo off
setlocal
pushd "%~dp0"
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
set "PHOTO_NO_BROWSER=1"

echo [1/2] Stopping OurTime safely...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop.ps1"
if errorlevel 1 goto :failed

echo [2/2] Starting OurTime...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1"
if errorlevel 1 goto :check_server
goto :ready

:check_server
echo Start script did not finish cleanly; checking http://127.0.0.1:8765 ...
set /a CHECK=0
:wait_loop
echo still waiting... (%CHECK%/20)
curl.exe --fail --silent --show-error --max-time 2 "http://127.0.0.1:8765/" >nul
if not errorlevel 1 goto :ready
set /a CHECK+=1
if %CHECK% GEQ 20 goto :failed
timeout /t 1 /nobreak >nul
goto :wait_loop

:ready
echo.
echo [OK] OurTime is running at http://127.0.0.1:8765
start "" "http://127.0.0.1:8765"
goto :done

:failed
echo.
echo [ERROR] Start or stop failed.
echo Check the messages above and data\server-error.log.

:done
echo.
pause
popd
endlocal
