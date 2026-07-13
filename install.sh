#!/usr/bin/env bash
# Instalador do servidor da Helena: prepara tudo para 'helena start'.
set -e
cd "$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"

echo "==> Instalando o servidor da Helena"

# 1. uv (gerenciador de ambiente/dependências)
if ! command -v uv >/dev/null 2>&1; then
  echo "==> uv não encontrado; instalando..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# 2. dependências (o uv provisiona o Python 3.14 automaticamente)
echo "==> Instalando dependências (uv sync)..."
uv sync

# 3. .env a partir do exemplo
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> .env criado a partir de .env.example"
fi

# 4. torna o wrapper executável
chmod +x helena

# 5. controle de desktop (opcional): mouse/teclado/tela
#    Windows/macOS/Linux-X11 já funcionam com o pyautogui/mss (via uv sync).
#    Linux Wayland precisa de ferramentas de sistema — instala best-effort.
if [ "$(uname)" = "Linux" ] && [ -n "$WAYLAND_DISPLAY" ]; then
  echo "==> Sessão Wayland detectada — configurando controle de desktop (pede sudo)..."
  ( set +e
    # 1. pacotes: tela (grim), teclado (wtype), mouse (ydotool)
    if command -v pacman >/dev/null 2>&1; then
      sudo pacman -S --needed --noconfirm grim wtype ydotool
    elif command -v apt-get >/dev/null 2>&1; then
      sudo apt-get install -y grim wtype ydotool
    elif command -v dnf >/dev/null 2>&1; then
      sudo dnf install -y grim wtype ydotool
    elif command -v zypper >/dev/null 2>&1; then
      sudo zypper install -y grim wtype ydotool
    else
      echo "   Gerenciador de pacotes não reconhecido — instale grim, wtype e ydotool à mão."
    fi
    # 2. módulo de kernel uinput (necessário p/ o mouse): carrega agora e no boot
    sudo modprobe uinput
    echo uinput | sudo tee /etc/modules-load.d/uinput.conf >/dev/null
    # 3. permissão do /dev/uinput
    echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' \
      | sudo tee /etc/udev/rules.d/80-helena-uinput.rules >/dev/null
    sudo groupadd -f input && sudo usermod -aG input "$USER"
    sudo udevadm control --reload-rules && sudo udevadm trigger
    # 4. daemon do ydotool como serviço de usuário (sem sudo)
    if systemctl --user enable --now ydotool.service 2>/dev/null; then
      echo "   * daemon ydotool ativo (serviço de usuário)."
    else
      echo "   * inicie o daemon manualmente: ydotoold &"
    fi
    echo "   * Se o MOUSE não responder de primeira, faça logout/login uma vez (grupo 'input')."
  ) || echo "   (setup de desktop pulou/falhou — o resto do servidor está ok)"
fi

echo
echo "✅ Pronto! Próximos passos:"
echo "   ./helena setup     # configura a chave do Gemini, porta, etc."
echo "   ./helena start     # inicia o servidor"
echo "   ./helena status    # confere se está no ar"
echo
echo "Dica: para usar de qualquer lugar, linke no PATH:"
echo "   sudo ln -s \"\$(pwd)/helena\" /usr/local/bin/helena"
