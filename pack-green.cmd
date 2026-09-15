@echo off
setlocal
call "%~dp0打包拾光.cmd" %*
exit /b %ERRORLEVEL%
