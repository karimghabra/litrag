@echo off
rem Removes litrag (uninstall.ps1 does it), never the libraries. Double-click this, or run
rem   uninstall.cmd [-Yes]
rem One block: cmd reads a block whole before it runs it, and uninstall.ps1 deletes the copy of
rem this file that install.ps1 left in the install folder while it is running.
(
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
  echo.
  pause
  exit /b
)
