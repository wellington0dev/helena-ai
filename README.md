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

## Chat no terminal (`helena chat`)

```bash
./helena chat
```

Login por email/senha na primeira vez (sessão fica salva localmente,
`chmod 600`). O prompt tem a mesma cara de um CLI de assistente moderno:

- **Multiline de verdade**: `Enter` envia, `Alt+Enter` quebra linha sem
  enviar — dá pra compor uma mensagem de várias linhas antes de mandar.
- **Histórico persistente**: seta ↑/↓ navega mensagens de sessões
  anteriores (fica em `data/cli_chat_history`), e sugestão fantasma
  (cinza) com base no que você já digitou antes.
- **Autocompletar**: comece a digitar `/` e os comandos disponíveis
  aparecem.
- **Colar grande texto**: acima de ~12 linhas/800 caracteres, o colado vira
  um placeholder tipo `[Texto colado #1 +240 linhas]` no terminal — só pra
  não poluir a tela; a Helena recebe o texto **completo** do mesmo jeito.
  Use `/colado <N>` pra conferir o que tinha por trás de um placeholder.
- **`/imagem`**: cola uma imagem da área de transferência (ex.: um
  screenshot copiado) e anexa na sua próxima mensagem. Depende de uma
  ferramenta de sistema pra ler o clipboard — Linux X11 precisa de
  `xclip` (`sudo apt install xclip`/equivalente; não instalado
  automaticamente), Linux Wayland usa `wl-paste` (já instalado pelo
  `install.sh` junto do controle de desktop), macOS/Windows não precisam
  instalar nada. Sem imagem no clipboard, ou sem a ferramenta, dá um aviso
  claro — nunca trava.
- `/historico`, `/logout`, `/sair` continuam funcionando como antes.

## Página web (chat + configuração)

Abra `http://localhost:<porta>/` (ou pelo IP da máquina, na rede local) —
login com a mesma conta do app, sem instalar nada:

- **Chat**: conversa com a Helena, igual ao app/`helena chat`.
- **Configurações**: tudo que o CLI configura — provider (Gemini/Ollama),
  chave do Gemini, modelos, catálogo colorido do Ollama (baixar/trocar/testar
  direto pela página), porta/host, notificação de desktop. O botão
  **"Salvar e reiniciar"** aplica e reinicia o servidor sozinho (funciona com
  `helena start` ou como serviço instalado — em `helena test`, modo dev sem
  pidfile, use `helena restart` no terminal).

Mesmo nível de acesso do chat: qualquer usuário logado pode ver/mudar a
configuração (segredos como a chave do Gemini nunca voltam em texto puro pro
navegador, só mascarados).

## Painel de desktop (Electron)

Uma janela nativa — grid com usuários ativos e jobs em segundo plano da
Helena, mais CPU/RAM/disco e os processos do sistema operacional. É uma
casca **fina**: só abre uma janela apontando pra `/dashboard` (a própria
Helena serve a página e os dados via API) — nenhuma lógica mora no lado
Electron.

Instalação (não é feita pelo `install.sh`/`install.ps1` — é opcional e
precisa de Node.js/npm, únicos neste projeto):

```bash
cd desktop-dashboard
npm install
```

Depois disso, peça pra Helena no chat: **"abre o painel"** / **"fecha o
painel"** (tools `abrir_dashboard`/`fechar_dashboard`, mesmo nível de
permissão de mouse/teclado — exige `fullcontrol`). Pra abrir manualmente sem
passar pela IA: `cd desktop-dashboard && npm start -- --url
http://127.0.0.1:<porta>/dashboard`.

Login é o mesmo `email`/senha de sempre — a janela do Electron tem seu
próprio perfil, então pede login na primeira vez e lembra depois. O painel
em si (`/dashboard`, `GET /dashboard/overview`) exige nível `principal`+
pra acessar (mostra atividade de outros usuários e processos do SO —
informação mais sensível que "minhas próprias configurações").

## `helena goal` — dê um propósito, a Helena pesquisa/planeja/implementa

```bash
./helena goal "quero gerenciar as finanças do meu freelance"
./helena goal   # sem argumento: pergunta o propósito no terminal
```

Mesmo login/sessão do `helena chat`. A Helena pesquisa o que for necessário
(ferramentas, integrações, credenciais), monta um plano numerado e **para,
esperando você aprovar** — só depois disso parte pra implementação de
verdade (instalar coisas, criar automações/comandos salvos, configurar).
Comandos no terminal: `/aguardar` (espera pesquisa/plano rodando em segundo
plano terminar), `/aprovar` (autoriza a implementação), `/historico`, `/sair`.

Cada ação sensível (shell/SSH) continua pedindo aprovação individual como em
qualquer conversa — o card aparece **direto no terminal** (permitir uma
vez / sempre / negar), sem precisar abrir o chat/app. Instalar uma
integração nova (ex.: um CLI de email, um SDK) é só mais um `executar_shell`
com aprovação — sem catálogo fixo do que ela pode tentar. Limitação honesta:
a Helena não completa fluxos de OAuth interativos (login no Gmail pelo
navegador, por exemplo) — ela prepara tudo em volta e pede a chave/token
que só você tem.

## Telegram (a Helena como bot)

A Helena pode virar um **bot completo no Telegram** — todas as funções do chat
funcionam por lá (texto, áudio, foto, documentos, geração de imagem/áudio/PDF,
lembretes, jobs em segundo plano e até aprovação de comandos de shell por botões).

1. Crie um bot no **[@BotFather](https://t.me/BotFather)** e copie o token.
2. Configure o token — pelo CLI ou pela página de configurações:
   ```bash
   ./helena config set TELEGRAM_BOT_TOKEN 123456:ABC-DEF...
   ./helena restart
   ```
   (Na página web: card **Telegram** → cole o token → **Salvar e reiniciar**.)
3. No Telegram, abra seu bot e mande **/login** — ele pede email e senha (a
   **mesma conta** do app/`helena chat`; a mensagem com a senha é apagada em
   seguida). Depois é só conversar.

Comandos do bot: `/login`, `/logout`, `/whoami`, `/historico` (espelha as últimas
mensagens da conversa), `/cancel`, `/help`.

Detalhes:
- **Sem URL pública**: usa long-polling, funciona atrás de NAT/sem porta aberta.
- **Histórico único**: a conversa é a mesma em todos os clientes (app, web, CLI,
  Telegram) — o que você fala num aparece no outro.
- **Permissões**: o nível da conta vale igual (só o usuário `principal`/
  `fullcontrol` controla a máquina). Comandos que pedem aprovação aparecem no
  Telegram com botões **Permitir / Negar / Sempre**.
- **Lembretes** e resultados de tarefas em segundo plano chegam no Telegram
  automaticamente (mesmo num servidor headless, sem toast de desktop).

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
- **sudo**: mesmo em `fullcontrol`, comandos com `sudo` são bloqueados por
  padrão — é uma permissão **separada**, opt-in:
  ```bash
  ./helena users sudo   <usuario>  # 🔓 habilita sudo (pergunta se aprovação é sempre obrigatória)
  ./helena users nosudo <usuario>  # desabilita
  ```
  Ao habilitar, o padrão é **sempre pedir aprovação no chat** pra qualquer
  comando com `sudo` — inclusive em `fullcontrol` e mesmo que aquele comando
  exato já tenha "Permitir sempre" concedido (o "sempre" não vale pra sudo
  nesse modo). Dá pra relaxar isso (`helena users sudo` de novo, respondendo
  "não" à pergunta) pra sudo seguir a mesma regra de confiança do resto do
  shell. Não há lista de comandos permitidos — a proteção real é aprovação +
  auditoria; uma allowlist textual é fácil de contornar (`sudo bash -c ...`).
  Isso só controla o que a **Helena tenta rodar** — se o comando de fato
  passa sem senha depende do `sudoers`/NOPASSWD já configurado no sistema
  operacional pra essa conta; a Helena nunca lida com prompt de senha (stdin
  fechado, como no SSH — se pedir senha, trava e falha por timeout em vez de
  travar silenciosamente). `helena panic` revoga sudo junto com os outros
  níveis.
- **Desktop (tela/mouse/teclado)**: `capturar_tela` (a IA VÊ a tela) exige
  `principal`; mover/clicar/digitar exigem `fullcontrol`.
  - **Windows / Linux-X11 / macOS**: funciona direto (pyautogui/mss, via `uv sync`).
  - **Linux Wayland**: precisa de `grim`/`wtype`/`ydotool` — o `install.sh` instala e
    configura o `/dev/uinput` (relogue depois; deixe o `ydotoold` rodando p/ o mouse).
  - ⚠️ Só funciona com o servidor rodando **na sessão gráfica logada** (não em
    VPS/headless/SSH — lá não há tela).

### Dar permissão total pra Helena (fullcontrol + sudo)

Pra Helena controlar a máquina por completo — qualquer comando, inclusive
com `sudo` — são 3 passos, cada um opt-in e reversível a qualquer momento:

```bash
./helena users fullcontrol <seu-email>   # 1. roda qualquer comando de shell sem aprovação
./helena users sudo        <seu-email>   # 2. libera sudo (pergunta: sempre pedir aprovação ou não)
```

Repare que os passos 1 e 2 usam o **email da conta na Helena**, não o
usuário do sistema operacional. O passo 2 sozinho ainda não basta: sudo de
verdade só funciona se a sua conta do SO já rodar `sudo` **sem pedir
senha** — a Helena nunca digita senha nenhuma (mesma regra do SSH). Se
ainda não tiver isso configurado, esse é o passo 3, no Linux:

```bash
# aqui é o USUÁRIO DO SISTEMA (ex.: weber), não o email
echo "<usuario-do-so> ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/<usuario-do-so>-nopasswd > /dev/null
sudo chmod 0440 /etc/sudoers.d/<usuario-do-so>-nopasswd
sudo visudo -c   # tem que aparecer "parsed OK" — se der erro, apague o arquivo na hora:
                 # sudo rm /etc/sudoers.d/<usuario-do-so>-nopasswd
```

⚠️ Isso libera sudo sem senha pra **qualquer coisa** nessa conta do
sistema, não só pra Helena — é uma decisão de segurança da sua máquina.
Pra um escopo mais restrito, troque `ALL` pelos comandos específicos que
você quer liberar, por exemplo:
```
<usuario-do-so> ALL=(ALL) NOPASSWD: /usr/bin/systemctl, /usr/bin/pacman
```
Confira se funcionou com `sudo -n true && echo "NOPASSWD ok"`.

Com os 3 passos feitos, a Helena ainda pede aprovação no chat pra cada
comando com `sudo` (o padrão `sudo_require_approval=1`, mais seguro). Pra
ela rodar sudo direto sem card, rode `helena users sudo <seu-email>` de
novo e responda "não" na pergunta de aprovação. `helena panic` revoga
fullcontrol e sudo dos dois, de uma vez, a qualquer momento.

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
| `TELEGRAM_BOT_TOKEN` | — | Token do bot do Telegram (@BotFather). Vazio = bot desligado. Veja [Telegram](#telegram-a-helena-como-bot). |
| `TELEGRAM_POLL_TIMEOUT` | `50` | Timeout (s) do long-polling do bot do Telegram. |
| `HELENA_ENV_FILE` | `<raiz>/.env` | Qual `.env` o CLI/página de configuração lê e escreve. Normalmente não precisa mexer — existe pra isolar os testes automatizados do `.env` real. |

## Notificações no desktop

Além da fila que o app Android puxa, o servidor também dispara a notificação
como **toast nativo do sistema operacional** onde ele está rodando — reminders,
"terminei sua tarefa", etc. Só funciona com o
servidor numa sessão gráfica logada (mesma exigência do controle de desktop
acima); em VPS/headless a tentativa falha silenciosamente. Desliga com
`HELENA_DESKTOP_NOTIFICATIONS=0`.

- **Linux**: via `notify-send` (pacote `libnotify-bin`/`libnotify`, geralmente
  já vem com o ambiente gráfico).
- **macOS**: via `osascript` (nativo, nada a instalar).
- **Windows**: via PowerShell (nativo). *Escrito, mas não testado em Windows.*

Os dados (banco SQLite, mídia, logs, pid) ficam em `data/` — fora do git.
