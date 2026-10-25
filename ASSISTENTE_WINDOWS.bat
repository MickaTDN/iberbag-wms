@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
 echo Execute primeiro INSTALAR_WINDOWS.bat.
 pause
 exit /b 1
)
.venv\Scripts\python.exe -m iberbag.windows_menu
pause
