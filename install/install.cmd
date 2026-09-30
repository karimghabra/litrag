@echo off
rem litrag's installer: install.ps1, beside this file, does the work. Double-click this, or run
rem   install.cmd [-Torch auto^|cpu^|cu130^|none] [-PrefetchModels] [-SkipOllama]
rem Running it again is the update.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "code=%ERRORLEVEL%"
echo.
pause
exit /b %code%
