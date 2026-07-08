@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\start_app.ps1"
if errorlevel 1 (
  echo.
  echo Impossible de lancer Otherme Clipper.
  pause
)
endlocal
