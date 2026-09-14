@echo off
setlocal
set "PY=C:\Users\A\PycharmProjects\ImageBrowser\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo 未找到打包用的 Python：%PY%
  exit /b 1
)
"%PY%" "%~dp0tools\pack_green.py" %*
exit /b %ERRORLEVEL%
