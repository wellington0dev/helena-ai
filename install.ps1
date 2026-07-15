# Instalador do servidor da Helena no Windows (PowerShell).
# Uso:  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "==> Instalando o servidor da Helena"

# 1. uv (gerenciador de ambiente/dependencias)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "==> uv nao encontrado; instalando..."
  powershell -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# 2. dependencias (o uv provisiona o Python 3.14 automaticamente)
Write-Host "==> Instalando dependencias (uv sync)..."
uv sync

# 3. .env a partir do exemplo
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "==> .env criado a partir de .env.example"
}

# Controle de desktop (mouse/teclado/tela) já funciona no Windows via pyautogui
# (instalado pelo uv sync) — nenhuma ferramenta de sistema extra é necessária.

Write-Host ""
Write-Host "OK! Proximos passos:"
Write-Host "   .\helena setup            # configura a chave do Gemini, porta, etc."
Write-Host "   .\helena test             # roda em 1o plano p/ testar (Ctrl+C sai)"
Write-Host "   .\helena service install  # instala como tarefa de logon (sobe ao entrar)"
Write-Host "   .\helena autoupdate on    # (opcional) atualizacao diaria pelo git"
