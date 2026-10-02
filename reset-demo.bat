@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\reset-demo.ps1"
if errorlevel 1 (
  echo Demo reset failed. Source code and non-demo databases were left untouched.
  pause
  exit /b 1
)
endlocal
