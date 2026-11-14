@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 -I -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1
if errorlevel 1 (
 echo Python 3.12 x64 indisponivel. Leia LEIA-ME_DDN_ENSAIO.txt.
 echo Canal oficial: https://www.python.org/downloads/windows/
 pause
 exit /b 1
)
py -3.12 -I windows\assistente.py
if errorlevel 1 echo Operacao nao concluida. Leia LEIA-ME_DDN_ENSAIO.txt.
pause
