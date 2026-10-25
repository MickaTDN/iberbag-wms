@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [IBERBAG WMS] Instalar dependencias Python no ambiente atual
py -m pip install -r requirements.txt
if errorlevel 1 (
 echo ERRO: Falha ao instalar dependencias. Verificar Python e Internet.
 pause
 exit /b 1
)
echo Instalacao das dependencias concluida.
pause
