@echo off
setlocal
cd /d "%~dp0"
if not exist caddy.exe (
 echo Coloque caddy.exe oficial nesta pasta. Leia o guia Windows.
 pause
 exit /b 1
)
caddy.exe run --config Caddyfile.EXEMPLO.txt --adapter caddyfile
pause
