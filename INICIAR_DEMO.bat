@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0IBERBAG_DEMO.exe" (
  "%~dp0IBERBAG_DEMO.exe" start
  if errorlevel 1 pause
  exit /b
)
if not exist "%~dp0demo-env\Scripts\python.exe" (
  echo Execute primeiro INSTALAR_DEMO.bat nesta pasta. A versao autonoma nao exige instalacao.
  pause
  exit /b 1
)
set "PYTHONPYCACHEPREFIX=%~dp0DEMO\cache"
set "PYTHONNOUSERSITE=1"
"%~dp0demo-env\Scripts\python.exe" -m iberbag_demo start --home "%~dp0."
if errorlevel 1 pause
