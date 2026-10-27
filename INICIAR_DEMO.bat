@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0demo-env\Scripts\python.exe" (
  echo Este pacote de codigo usa Python oficial. Execute primeiro INSTALAR_DEMO.bat.
  echo Nao liberte nem execute ficheiros bloqueados pelo antivirus.
  pause
  exit /b 1
)
"%~dp0demo-env\Scripts\python.exe" -I -B -c "import sys; sys.path.insert(0,sys.argv.pop(1)); from iberbag_demo.__main__ import main; raise SystemExit(main())" "%~dp0." start --home "%~dp0."
if errorlevel 1 pause
