# Helena — Servidor

Backend da **Helena**, uma assistente pessoal com IA (Flask + SQLite + Gemini).
Este repositório é **só o servidor** (a API que a IA roda). O app Android que
conversa com ela é um projeto separado — aponte-o para o endereço deste servidor.

O servidor foi feito para rodar num computador/VPS sempre ligado.

---

## Requisitos

- **Linux, macOS ou Windows**, com `git`.
- Nada mais: o instalador cuida do resto (instala o [uv](https://astral.sh/uv),
  que provisiona automaticamente o Python 3.14 e as dependências).
- Uma **chave da API do Google Gemini** — gratuita em <https://ai.google.dev/>.

## Instalação

**Linux / macOS:**

```bash
git clone <url-do-repositorio> helena-server
cd helena-server
./install.sh
```

**Windows (PowerShell):**

```powershell
git clone <url-do-repositorio> helena-server
cd helena-server
powershell -ExecutionPolicy Bypass -File install.ps1
```

O instalador instala o uv (se faltar), baixa as dependências (`uv sync`) e
cria o `.env`. No restante deste guia, onde estiver `./helena`, no Windows use
`.\helena` (o `helena.cmd`) — os comandos são os mesmos.

## Configuração

```bash
./helena setup
```

Pergunta a **chave do Gemini** e a **porta** (default 5000), e gera um segredo
JWT automaticamente. Para também escolher modelos/voz: `./helena setup --advanced`.

Sem interação:

```bash
./helena config set GEMINI_API_KEY sua-chave-aqui
./helena config list          # mostra tudo (segredos mascarados)
./helena config get HELENA_PORT
```

## Uso

```bash
./helena test       # roda em 1º plano p/ testar antes de instalar (Ctrl+C sai)
./helena start      # inicia em background
./helena status     # está rodando? saúde? url?
./helena logs -f    # acompanha o log
./helena restart    # reinicia
./helena stop       # para
./helena doctor     # checa pré-requisitos e estado
```

Depois de `start`, a API fica em `http://localhost:<porta>` (e na rede local
pelo IP da máquina, já que o bind é `0.0.0.0` por padrão). Configure esse
endereço no app Android.

## Rodar junto do sistema (serviço)

Instala como serviço que sobe sozinho ao logar:

```bash
./helena service install     # instala e inicia
./helena service status      # estado
./helena service uninstall   # remove
```

- **Linux**: serviço **systemd de usuário** (não root) — de propósito, porque só
  assim ele enxerga a sessão gráfica (controle de tela/mouse não funciona num
  serviço de sistema). ⚠️ Valide o controle de desktop após um **logout/login** real.
- **Windows**: **tarefa no logon** (não Windows Service, que roda na Session 0
  isolada do desktop). *Escrito, mas não testado em Windows.*

Com o serviço instalado, `helena start/stop/status` passam a operar sobre ele.

## Atualizar

```bash
./helena update git      # puxa do remoto (git pull) + uv sync + reinicia se mudou
./helena update code     # aplica mudanças que VOCÊ fez no código local (uv sync + reinicia)
./helena autoupdate on   # (opcional) auto-update diário pelo git; 'off' desliga
```

`update git` só age num clone git com árvore limpa e branch remoto configurado.
`update code` serve para árvore com alterações locais (não mexe no git).

## Usar de qualquer diretório (opcional)

**Linux / macOS** — link no PATH:

```bash
sudo ln -s "$(pwd)/helena" /usr/local/bin/helena
helena status   # agora funciona de qualquer lugar
```

**Windows** — adicione a pasta do projeto ao `Path` do usuário (Configurações →
Variáveis de ambiente), e então `helena status` funciona de qualquer lugar.

## Controle do computador (shell + desktop)

A Helena pode controlar a máquina onde roda: executar comandos no shell e
(opcional) controlar tela/mouse/teclado. Por segurança há **níveis de permissão
por usuário**, definidos pelo CLI:

```bash
./helena users                        # lista os usuários e o nível de cada um
./helena users principal   <usuario>  # pode pedir comandos (com aprovação no chat)
./helena users fullcontrol <usuario>  # ⚡ roda QUALQUER comando SEM aprovação
./helena users normal      <usuario>  # não controla nada (padrão)
```

- **Shell**: um usuário `principal` pede um comando → aparece um card no chat com
  **Permitir / Negar / Permitir sempre**. Em `fullcontrol`, roda direto (a saída
  ainda aparece no chat). Rails: timeout, stdin fechado, `cwd`=home, log de auditoria.
- **Desktop (tela/mouse/teclado)**: `capturar_tela` (a IA VÊ a tela) exige
  `principal`; mover/clicar/digitar exigem `fullcontrol`.
  - **Windows / Linux-X11 / macOS**: funciona direto (pyautogui/mss, via `uv sync`).
  - **Linux Wayland**: precisa de `grim`/`wtype`/`ydotool` — o `install.sh` instala e
    configura o `/dev/uinput` (relogue depois; deixe o `ydotoold` rodando p/ o mouse).
  - ⚠️ Só funciona com o servidor rodando **na sessão gráfica logada** (não em
    VPS/headless/SSH — lá não há tela).

## Variáveis de ambiente

Ficam no `.env` (não versionado). Veja `.env.example`.

| Variável | Default | Descrição |
|---|---|---|
| `GEMINI_API_KEY` | — | **Obrigatória.** Chave da API do Gemini. |
| `JWT_SECRET_KEY` | (gerado) | Segredo para assinar tokens. Gerado pelo `setup`. |
| `HELENA_PORT` | `5000` | Porta HTTP. |
| `HELENA_HOST` | `0.0.0.0` | Interface de bind. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Modelo do agente. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | Geração de imagem. |
| `GEMINI_TTS_MODEL` | `gemini-2.5-flash-preview-tts` | TTS (voz). |
| `GEMINI_TTS_VOICE` | `Kore` | Voz do TTS. |
| `HELENA_DATA_DIR` | `./data` | Diretório de dados (SQLite). |
| `HELENA_MEDIA_DIR` | `./data/media` | Diretório de mídia. |

Os dados (banco SQLite, mídia, logs, pid) ficam em `data/` — fora do git.
