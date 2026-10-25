@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pip check
if errorlevel 1 exit /b 1
echo Instalacao concluida. Leia docs\INSTALACAO_WINDOWS.md.
pause
