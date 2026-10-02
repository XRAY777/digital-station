@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-demo.ps1"
if errorlevel 1 (
  echo Digital Station failed to start. Check the message above and logs\*.err.log.
  pause
  exit /b 1
)
endlocal
