@echo off
REM Wrapper do CLI da Helena no Windows: ancorado ao diretorio deste arquivo
REM (funciona chamado de qualquer lugar).
setlocal
cd /d "%~dp0"

REM Preferencia 1: o python de dentro do .venv ja provisionado. Nao depende
REM de 'uv'/'python' estarem no PATH desta sessao -- e o caminho mais
REM confiavel, porque um terminal recem-aberto no Windows as vezes ainda nao
REM enxerga uma atualizacao de PATH feita por uma instalacao anterior (e foi
REM exatamente isso que fazia parecer que a Helena "sumia" ao reabrir o
REM terminal). O .venv em disco nao tem esse problema.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" cli.py %*
  exit /b %errorlevel%
)

REM Preferencia 2: 'uv' no PATH desta sessao (1a instalacao, .venv ainda nao existe)
where uv >nul 2>&1
if %errorlevel%==0 (
  uv run python cli.py %*
  exit /b %errorlevel%
)

REM Preferencia 3: 'uv' no local padrao de instalacao, mesmo que o PATH desta
REM sessao ainda nao tenha sido atualizado
if exist "%USERPROFILE%\.local\bin\uv.exe" (
  "%USERPROFILE%\.local\bin\uv.exe" run python cli.py %*
  exit /b %errorlevel%
)

REM Ultimo recurso: python do sistema. cli.py e stdlib-only de proposito
REM (funciona sem as deps do app), entao pelo menos 'helena setup'/'doctor'
REM funcionam -- mas comandos que sobem o servidor vao pedir pra rodar
REM 'install.ps1' de novo.
where python >nul 2>&1
if %errorlevel%==0 (
  python cli.py %*
  exit /b %errorlevel%
)

echo Nao encontrei nem o ambiente da Helena (.venv) nem 'uv'/'python' no PATH.
echo Rode install.ps1 de novo (ou abra um terminal COMPLETAMENTE novo, nao so
echo uma aba, se acabou de instalar).
exit /b 1
