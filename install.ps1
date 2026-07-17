# Instalador do servidor da Helena no Windows (PowerShell).
# Uso:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# $ErrorActionPreference só cobre erros de CMDLET do PowerShell — um executor
# externo (uv.exe, o instalador do uv) que falha com código de saída != 0 NÃO
# interrompe o script sozinho. Sem checar $LASTEXITCODE explicitamente depois
# de cada um, uma falha no meio passa em silêncio e o script termina
# imprimindo "OK!" mesmo com a instalação pela metade — exatamente o sintoma
# de "parecia instalado, mas não tá".
function Assert-LastExitCode($step) {
  if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERRO: '$step' falhou (código $LASTEXITCODE). Instalação interrompida." -ForegroundColor Red
    exit 1
  }
}

Write-Host "==> Instalando o servidor da Helena"

# 1. uv (gerenciador de ambiente/dependencias)
$localBin = "$env:USERPROFILE\.local\bin"
$uvExe = "$localBin\uv.exe"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "==> uv não encontrado; instalando..."
  powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  Assert-LastExitCode "instalação do uv"

  # Persiste o PATH pro usuário (registro), não só nesta sessão — o instalador
  # do uv normalmente já faz isso, mas reforçamos aqui: sem essa persistência,
  # fechar e abrir um terminal NOVO faz o 'uv' desaparecer de novo, e o
  # 'helena.cmd' (que também tenta achar 'uv' no PATH) parece ter "esquecido"
  # que tudo já foi instalado.
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath -notlike "*$localBin*") {
    [Environment]::SetEnvironmentVariable("Path", "$localBin;$userPath", "User")
  }
  $env:Path = "$localBin;$env:Path"  # também nesta sessão, sem esperar reabrir o terminal

  if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Test-Path $uvExe) {
      # ainda não está no PATH desta sessão por algum motivo, mas o binário
      # existe — usa o caminho direto pros próximos passos deste script
      Set-Alias -Name uv -Value $uvExe -Scope Script
    } else {
      Write-Host "ERRO: uv foi 'instalado' mas $uvExe não existe. Baixe manualmente: https://astral.sh/uv" -ForegroundColor Red
      exit 1
    }
  }
}

# 2. dependencias (o uv provisiona o Python 3.14 automaticamente)
Write-Host "==> Instalando dependências (uv sync)..."
uv sync
Assert-LastExitCode "uv sync"

# Verificação final: é ISSO que importa de verdade (não "o comando rodou sem
# erro visível"), e é o mesmo arquivo que cli.py/_server_python() e
# helena.cmd vão procurar depois — se não existir aqui, nada mais vai
# funcionar, então é melhor falhar AGORA com uma mensagem clara.
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Write-Host "ERRO: 'uv sync' terminou mas .venv\Scripts\python.exe não existe — algo falhou." -ForegroundColor Red
  Write-Host "Rode 'uv sync' manualmente pra ver o erro completo." -ForegroundColor Red
  exit 1
}

# 3. .env a partir do exemplo
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "==> .env criado a partir de .env.example"
}

# Controle de desktop (mouse/teclado/tela) já funciona no Windows via pyautogui
# (instalado pelo uv sync) — nenhuma ferramenta de sistema extra é necessária.

Write-Host ""
Write-Host "OK! Ambiente verificado (.venv funcional). Próximos passos:" -ForegroundColor Green
Write-Host "   .\helena setup            # configura a chave do Gemini, porta, etc."
Write-Host "   .\helena test             # roda em 1o plano p/ testar (Ctrl+C sai)"
Write-Host "   .\helena service install  # instala como tarefa de logon (sobe ao entrar)"
Write-Host "   .\helena autoupdate on    # (opcional) atualizacao diaria pelo git"
Write-Host ""
Write-Host "Se abrir um terminal NOVO depois e 'helena'/'uv' parecerem não reconhecidos:" -ForegroundColor Yellow
Write-Host "feche TODAS as janelas do terminal (não só a aba/tab) e abra de novo — o" -ForegroundColor Yellow
Write-Host "Windows às vezes só atualiza variáveis de ambiente em processos totalmente" -ForegroundColor Yellow
Write-Host "novos. O '.\helena' já funciona mesmo assim (ele acha o ambiente sozinho)." -ForegroundColor Yellow
