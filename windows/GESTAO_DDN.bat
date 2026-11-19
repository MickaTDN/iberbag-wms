@echo off
setlocal
powershell.exe -NoProfile -File "%~dp0windows\gestao_launcher.ps1"
if errorlevel 1 echo Gestao nao iniciada; nao alterar politicas de seguranca.
pause
