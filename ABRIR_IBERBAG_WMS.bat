@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
title DDN WMS - Instalacao e testes locais

if not exist "iberbag\cli.py" (
 echo.
 echo ERRO: Nao foi encontrado iberbag\cli.py nesta pasta.
 echo Extraia TODO o conteudo do ZIP para uma pasta nova.
 echo Nao mova este ficheiro BAT para fora dessa pasta.
 echo A pasta iberbag deve ficar ao lado deste ficheiro BAT.
 echo.
 pause
 exit /b 1
)

where py >nul 2>&1
if not errorlevel 1 (
 set "PY=py"
 goto menu
)
where python >nul 2>&1
if not errorlevel 1 (
 set "PY=python"
 goto menu
)
echo ERRO: Python nao encontrado.
echo Instale Python 3.11 ou superior e volte a abrir este ficheiro.
pause
exit /b 1

:menu
cls
echo =========================================================
echo             DDN WMS v2.3 - TESTE LOCAL
 echo =========================================================
echo Pasta: %CD%
echo.
echo 1 - Instalar bibliotecas Python (primeira vez)
echo 2 - Validar dados do Excel (nao grava)
echo 3 - Importar dados do Excel (grava na base de testes)
echo 4 - Criar conta de administrador
echo 5 - Iniciar programa no computador local
echo 6 - Verificar estrutura da instalacao
echo 0 - Sair
echo.
set "OPCAO="
set /p "OPCAO=Escolha uma opcao: "
if "%OPCAO%"=="1" goto instalar
if "%OPCAO%"=="2" goto validar
if "%OPCAO%"=="3" goto importar
if "%OPCAO%"=="4" goto administrador
if "%OPCAO%"=="5" goto iniciar
if "%OPCAO%"=="6" goto verificar
if "%OPCAO%"=="0" exit /b 0
goto menu

:instalar
echo.
"%PY%" -m pip install -r "requirements.txt"
if errorlevel 1 (
 echo Falha na instalacao de bibliotecas. Verifique a mensagem acima.
) else (
 echo Bibliotecas instaladas.
)
pause
goto menu

:obter_excel
set "FICHEIRO_EXCEL="
if exist "%CD%\IBERBAG.xlsm" (
 set "FICHEIRO_EXCEL=%CD%\IBERBAG.xlsm"
 goto :eof
)
echo.
echo Copie IBERBAG.xlsm para esta pasta OU introduza o caminho completo.
echo Exemplo: C:\Utilizadores\Publico\Documentos\IBERBAG.xlsm
set /p "FICHEIRO_EXCEL=Caminho completo do Excel: "
rem Retirar aspas que o Explorador possa ter acrescentado.
set "FICHEIRO_EXCEL=%FICHEIRO_EXCEL:"=%"
if not exist "%FICHEIRO_EXCEL%" (
 echo ERRO: Excel nao encontrado nesse caminho.
 set "FICHEIRO_EXCEL="
)
goto :eof

:validar
call :obter_excel
if not defined FICHEIRO_EXCEL goto pause_menu
"%PY%" -m iberbag.cli import-excel "%FICHEIRO_EXCEL%"
goto pause_menu

:importar
call :obter_excel
if not defined FICHEIRO_EXCEL goto pause_menu
echo.
echo PRIMEIRO: VALIDACAO SEM GRAVAR
"%PY%" -m iberbag.cli import-excel "%FICHEIRO_EXCEL%"
if errorlevel 1 goto pause_menu
echo.
echo ATENCAO: esta operacao grava no banco de dados de testes.
echo Nao repita a importacao se ja foi realizada.
set "RESPOSTA="
set /p "RESPOSTA=Escreva IMPORTAR para confirmar: "
if not "%RESPOSTA%"=="IMPORTAR" (
 echo Importacao cancelada.
 goto pause_menu
)
"%PY%" -m iberbag.cli import-excel "%FICHEIRO_EXCEL%" --apply
goto pause_menu

:administrador
echo.
echo Criar conta ADMINISTRADOR (palavra-passe minima 12 caracteres)
"%PY%" -m iberbag.cli create-admin
goto pause_menu

:iniciar
echo.
echo Iniciar programa APENAS neste computador.
echo Abra: http://127.0.0.1:8000
echo Para parar: CTRL+C nesta janela.
echo.
set "WMS_COOKIE_SECURE=0"
"%PY%" -m uvicorn iberbag.app:app --host 127.0.0.1 --port 8000
goto pause_menu

:verificar
echo.
if exist "iberbag\cli.py" (echo [OK] iberbag\cli.py) else (echo [ERRO] iberbag\cli.py)
if exist "iberbag\app.py" (echo [OK] iberbag\app.py) else (echo [ERRO] iberbag\app.py)
if exist "requirements.txt" (echo [OK] requirements.txt) else (echo [ERRO] requirements.txt)
"%PY%" -c "import iberbag.cli; print('[OK] Python encontra o modulo iberbag.cli')"
if errorlevel 1 echo [ERRO] Verifique que extraiu o ZIP inteiro para esta pasta.
goto pause_menu

:pause_menu
echo.
pause
goto menu
