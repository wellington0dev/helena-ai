@echo off
REM Wrapper do CLI da Helena no Windows: roda o cli.py dentro do ambiente uv,
REM ancorado ao diretorio deste arquivo (funciona chamado de qualquer lugar).
setlocal
cd /d "%~dp0"
where uv >nul 2>&1
if %errorlevel%==0 (
  uv run python cli.py %*
) else (
  python cli.py %*
)
