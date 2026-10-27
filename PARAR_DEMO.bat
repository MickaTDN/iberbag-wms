@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0IBERBAG_DEMO.exe" (
  "%~dp0IBERBAG_DEMO.exe" stop
  if errorlevel 1 pause
  exit /b
)
if not exist "%~dp0demo-env\Scripts\python.exe" (
  echo A demonstracao nao esta instalada nesta pasta.
  pause
  exit /b 1
)
set "PYTHONPYCACHEPREFIX=%~dp0DEMO\cache"
set "PYTHONNOUSERSITE=1"
"%~dp0demo-env\Scripts\python.exe" -m iberbag_demo stop --home "%~dp0."
if errorlevel 1 pause
