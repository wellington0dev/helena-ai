# Helena — Servidor

Backend da **Helena**, uma assistente pessoal com IA (Flask + SQLite +
Gemini ou um modelo local via Ollama). Este repositório é o servidor — a API
que a IA roda, com CLI de administração e uma página web própria (chat +
configuração). O app Android é um projeto separado — aponte-o para o
endereço deste servidor.

O servidor foi feito para rodar num computador/VPS sempre ligado.

Quer construir seu próprio cliente (outro app, script, integração)? Veja a
[referência completa da API](API.md) — rotas, autenticação, WebSocket.

---

## Requisitos

- **Linux, macOS ou Windows**, com `git`.
- Nada mais: o instalador cuida do resto (instala o [uv](https://astral.sh/uv),
  que provisiona automaticamente o Python 3.14 e as dependências).
- Um cérebro pra IA — escolha um no `helena setup`:
  - **Gemini** (nuvem): uma **chave da API do Google Gemini** — gratuita em
    <https://ai.google.dev/>; ou
  - **Modelo local** (Ollama): roda na sua própria máquina, sem chave nem
    custo de API — o setup instala o Ollama e baixa o modelo pra você.

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

Interativo (menus de seta — funciona em qualquer terminal, cai pra digitar
número se o terminal não suportar): primeiro pergunta **qual cérebro** usar —

- **Gemini**: pede a chave da API.
- **Modelo local (Ollama)**: instala o Ollama se faltar (pede confirmação),
  detecta RAM/CPU/GPU da sua máquina e mostra um catálogo de modelos com
  recomendação colorida (🟢 adequado / 🟡 roda mas custa desempenho / 🔴 não
  recomendado pra essa máquina), baixa o escolhido e testa uma geração de
  verdade antes de confirmar — pra nunca deixar configurado algo que não
  funciona.

Depois pergunta a **porta** (default 5000) e gera um segredo JWT
automaticamente. Pra também escolher modelos/voz do Gemini: `./helena setup --advanced`.

Trocar de cérebro depois, sem rodar o setup inteiro de novo:

```bash
./helena provider              # mostra o atual + menu pra trocar
./helena provider gemini       # troca direto
./helena provider ollama       # troca direto (pede modelo se ainda não tiver um)
./helena models list           # catálogo colorido + o que já foi baixado
./helena models use <nome>     # baixa (se preciso) e ativa um modelo
./helena models pull <nome>    # só baixa
./helena models remove <nome>  # remove um modelo baixado
```

Sem interação:

```bash
./helena config set GEMINI_API_KEY sua-chave-aqui
./helena config list          # mostra tudo (segredos mascarados)
./helena config get HELENA_PORT
```

Tudo isso também dá pra fazer pela [página web](#página-web-chat--configuração).

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

Qualquer subcomando que espera uma ação (`users`, `service`, `update`,
`autoupdate`, `config`, `models`, `provider`...) mostra um menu de setas
quando chamado sem argumento — digitar o comando exato continua funcionando
igual, é só mais uma forma de usar.

Depois de `start`, a API fica em `http://localhost:<porta>` (e na rede local
pelo IP da máquina, já que o bind é `0.0.0.0` por padrão). Configure esse
endereço no app Android, ou abra direto no navegador (veja abaixo).

## Página web (chat + configuração)

Abra `http://localhost:<porta>/` (ou pelo IP da máquina, na rede local) —
login com a mesma conta do app, sem instalar nada:

- **Chat**: conversa com a Helena, igual ao app/`helena chat`.
- **Configurações**: tudo que o CLI configura — provider (Gemini/Ollama),
  chave do Gemini, modelos, catálogo colorido do Ollama (baixar/trocar/testar
  direto pela página), porta/host, notificação de desktop, federação. O botão
  **"Salvar e reiniciar"** aplica e reinicia o servidor sozinho (funciona com
  `helena start` ou como serviço instalado — em `helena test`, modo dev sem
  pidfile, use `helena restart` no terminal).

Mesmo nível de acesso do chat: qualquer usuário logado pode ver/mudar a
configuração (segredos como a chave do Gemini nunca voltam em texto puro pro
navegador, só mascarados).

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
  ainda aparece no chat). Rails: timeout, stdin fechado, log de auditoria, e
  `cwd` = o **diretório de trabalho atual** (veja [Trabalho em código](#trabalho-em-código-diretório-comandos-memória-de-projeto);
  cai pro home se nenhum estiver definido).
- **Desktop (tela/mouse/teclado)**: `capturar_tela` (a IA VÊ a tela) exige
  `principal`; mover/clicar/digitar exigem `fullcontrol`.
  - **Windows / Linux-X11 / macOS**: funciona direto (pyautogui/mss, via `uv sync`).
  - **Linux Wayland**: precisa de `grim`/`wtype`/`ydotool` — o `install.sh` instala e
    configura o `/dev/uinput` (relogue depois; deixe o `ydotoold` rodando p/ o mouse).
  - ⚠️ Só funciona com o servidor rodando **na sessão gráfica logada** (não em
    VPS/headless/SSH — lá não há tela).

## Trabalho em código (diretório, comandos, memória de projeto)

Para quem tem controle de shell (`principal`/`fullcontrol`), a Helena ganha
capacidades de assistente de código. Elas são **subordinadas ao papel de
assistente pessoal** — acionadas só quando o pedido é sobre código, sem mudar o
tom dela — e se apoiam umas nas outras:

- **Diretório de trabalho**: o `helena chat` envia o diretório do seu terminal a
  cada mensagem, então a Helena já trabalha de onde você está — comandos de shell
  e edições de código rodam ali (não mais sempre no home). Dentro da conversa ela
  navega de forma persistente com a ferramenta `mudar_diretorio`, e o diretório
  atual aparece no contexto dela.
- **Biblioteca de comandos**: um catálogo interno de comandos de terminal (o que
  fazem, flags úteis e como combiná-los com pipes), que ela consulta **sob
  demanda** (ferramenta `buscar_comando`) em vez de carregar tudo no contexto —
  já filtrado pelo sistema operacional da máquina. Fica hardcoded em
  `app/agent/command_library.py`.
- **Memória de projeto (`.helena/`)**: antes de criar ou modificar um arquivo, a
  Helena decide se o diretório é um projeto de programação (código-fonte, `.git`,
  ou manifestos como `package.json`/`pyproject.toml`/`Cargo.toml`/`go.mod`). Se
  for, ela mantém uma pasta `.helena/` na raiz do projeto com o que aprendeu —
  linguagem, framework, comandos, git, estrutura e contexto por arquivo —
  **navegável em JSON** (ferramenta `projeto`: `mapa`/`ler`/`buscar`/`salvar`)
  pra retomar o trabalho sem reescanear tudo e sem inchar o contexto em tokens.
  É **local por padrão**: a Helena escreve um `.helena/.gitignore` com `*`, então
  nada dessa memória entra no git do seu projeto.

Nada disso aparece para usuários `normal` — as ferramentas só são oferecidas ao
modelo quando o usuário tem o nível de permissão necessário.

## Modelo local (Ollama)

Com `LLM_PROVIDER=ollama`, o cérebro da Helena (chat, tarefas em segundo
plano, resumo de conversa, memória de longo prazo) roda 100% local via
[Ollama](https://ollama.com) — sem chave, sem custo de API, sem depender de
internet pro "pensar". Configurado pelo `helena setup` ou `helena
provider`/`models` (veja [Configuração](#configuração)).

**Limitações** (o Ollama não faz — ficam indisponíveis em modo local, sem
quebrar o resto): geração de imagem, TTS (voz) e descrição de foto/áudio
enviados continuam exigindo `GEMINI_API_KEY` configurada também, mesmo com
`LLM_PROVIDER=ollama`. Visão (a IA "ver" a tela numa tarefa de desktop) é
melhor-esforço — só funciona se o modelo escolhido for multimodal E suportar
tools ao mesmo tempo, combinação rara. O catálogo (`helena models list`) só
lista modelos com suporte a tool-calling confirmado — sem isso a Helena não
consegue usar nenhuma ferramenta (lembrete, shell, etc.).

O daemon (`ollama serve`) sobe e desce **junto do ciclo de vida da Helena**
automaticamente — seja rodando via `helena start` ou como serviço instalado
— sem precisar geri-lo à parte. Se você já roda o Ollama por conta própria
(outro serviço, outro app usando ele também), desligue esse acoplamento com
`OLLAMA_MANAGED=0` — a Helena só passa a se conectar ao que já estiver
rodando em `OLLAMA_HOST`, nunca sobe nem derruba nada.

## Variáveis de ambiente

Ficam no `.env` (não versionado). Veja `.env.example`.

| Variável | Default | Descrição |
|---|---|---|
| `LLM_PROVIDER` | `gemini` | Cérebro da Helena: `gemini` ou `ollama`. |
| `GEMINI_API_KEY` | — | Chave da API do Gemini. Obrigatória se `LLM_PROVIDER=gemini`; recomendada mesmo em `ollama` (imagem/TTS/mídia). |
| `JWT_SECRET_KEY` | (gerado) | Segredo para assinar tokens. Gerado pelo `setup`. |
| `HELENA_PORT` | `5000` | Porta HTTP. |
| `HELENA_HOST` | `0.0.0.0` | Interface de bind. |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Modelo do agente. |
| `GEMINI_IMAGE_MODEL` | `gemini-2.5-flash-image` | Geração de imagem. |
| `GEMINI_TTS_MODEL` | `gemini-2.5-flash-preview-tts` | TTS (voz). |
| `GEMINI_TTS_VOICE` | `Kore` | Voz do TTS. |
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Endereço do daemon Ollama. |
| `OLLAMA_MODEL` | — | Modelo local ativo (tag do `ollama pull`, ex.: `qwen2.5:7b`). |
| `OLLAMA_MANAGED` | `1` | Se a Helena sobe/derruba o `ollama serve` junto do próprio ciclo de vida (`0` desliga). |
| `OLLAMA_REQUEST_TIMEOUT_SECONDS` | `300` | Timeout da chamada HTTP ao Ollama (modelos locais podem ser lentos). |
| `HELENA_DATA_DIR` | `./data` | Diretório de dados (SQLite). |
| `HELENA_MEDIA_DIR` | `./data/media` | Diretório de mídia. |
| `HELENA_DESKTOP_NOTIFICATIONS` | `1` | Notificação nativa do SO onde o servidor roda (`0` desliga). |
| `HELENA_ENV_FILE` | `<raiz>/.env` | Qual `.env` o CLI/página de configuração lê e escreve. Normalmente não precisa mexer — existe pra isolar os testes automatizados do `.env` real. |

## Notificações no desktop

Além da fila que o app Android puxa, o servidor também dispara a notificação
como **toast nativo do sistema operacional** onde ele está rodando — reminders,
"terminei sua tarefa", mensagens de peers federados, etc. Só funciona com o
servidor numa sessão gráfica logada (mesma exigência do controle de desktop
acima); em VPS/headless a tentativa falha silenciosamente. Desliga com
`HELENA_DESKTOP_NOTIFICATIONS=0`.

- **Linux**: via `notify-send` (pacote `libnotify-bin`/`libnotify`, geralmente
  já vem com o ambiente gráfico).
- **macOS**: via `osascript` (nativo, nada a instalar).
- **Windows**: via PowerShell (nativo). *Escrito, mas não testado em Windows.*

Os dados (banco SQLite, mídia, logs, pid) ficam em `data/` — fora do git.
