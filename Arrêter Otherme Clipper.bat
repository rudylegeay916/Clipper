@echo off
setlocal
set "ROOT=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\stop_app.ps1"
if errorlevel 1 (
  echo.
  echo Impossible d'arreter Otherme Clipper.
  pause
)
endlocal
