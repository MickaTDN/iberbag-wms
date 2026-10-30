@echo off
chcp 65001 >nul
cd /d "%~dp0"
if defined WMS_DATABASE_URL (
 echo AVISO: WMS_DATABASE_URL foi configurado. Este teste usara a base de dados indicada.
 echo Para usar SQLite de desenvolvimento, remova essa variavel nesta consola.
)
set "WMS_COOKIE_SECURE=0"
echo DDN WMS - TESTE LOCAL (nao aceder a partir da rede)
echo Abra http://127.0.0.1:8000
py -m uvicorn iberbag.app:app --host 127.0.0.1 --port 8000
pause
