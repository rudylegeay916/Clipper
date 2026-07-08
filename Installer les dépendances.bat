@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\setup_windows.ps1"
if errorlevel 1 (
  echo.
  echo Installation incomplete.
  pause
)
endlocal
