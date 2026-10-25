@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not defined WMS_DATABASE_URL (
 echo ERRO: configurar WMS_DATABASE_URL para PostgreSQL antes de arrancar em rede.
 pause
 exit /b 1
)
echo %WMS_DATABASE_URL% | findstr /b /c:"postgresql+psycopg://" >nul
if errorlevel 1 (
 echo ERRO: para rede e obrigatorio PostgreSQL: postgresql+psycopg://...
 pause
 exit /b 1
)
set "WMS_COOKIE_SECURE=1"
echo Iberbag WMS - servidor em 127.0.0.1:8000
 echo ATENCAO: disponibilizar na rede apenas atraves de proxy HTTPS corretamente configurado.
py -m uvicorn iberbag.app:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips 127.0.0.1
pause
