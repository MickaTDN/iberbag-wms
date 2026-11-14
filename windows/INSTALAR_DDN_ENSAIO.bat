@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
py -3.12 -I windows\assistente.py
if errorlevel 1 echo Operacao nao concluida. Leia LEIA-ME_DDN_ENSAIO.txt.
pause
