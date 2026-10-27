@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0IBERBAG_DEMO.exe" (
  echo Este pacote ja inclui tudo. Execute INICIAR_DEMO.bat.
  pause
  exit /b 0
)
where py >nul 2>nul
if errorlevel 1 (
  echo Esta alternativa precisa de Python 3.12, instalado apenas para o utilizador.
  echo Prefira o ZIP autonomo do workflow DEMO Windows, que nao precisa de Python.
  pause
  exit /b 1
)
if not exist "%~dp0DEMO" mkdir "%~dp0DEMO"
if not exist "%~dp0DEMO\temporarios" mkdir "%~dp0DEMO\temporarios"
set "TEMP=%~dp0DEMO\temporarios"
set "TMP=%~dp0DEMO\temporarios"
set "PYTHONPYCACHEPREFIX=%~dp0DEMO\cache"
set "PYTHONNOUSERSITE=1"
set "PIP_CONFIG_FILE=NUL"
set "PIP_NO_CACHE_DIR=1"
py -3.12 -m venv "%~dp0demo-env"
if errorlevel 1 goto failed
"%~dp0demo-env\Scripts\python.exe" -m pip --isolated install --disable-pip-version-check --no-cache-dir -r "%~dp0requirements-demo.txt"
if errorlevel 1 goto failed
echo Instalacao local terminada. Execute INICIAR_DEMO.bat.
pause
exit /b 0
:failed
echo A instalacao local falhou. Nao altere o WMS operacional. Consulte LEIA-ME_DEMO.txt.
pause
exit /b 1
