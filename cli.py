#!/usr/bin/env python3
"""CLI da Helena — configurar, rodar, parar, checar e atualizar o servidor.

Depende só da stdlib para funcionar mesmo antes das dependências do app estarem
instaladas (ex.: `helena setup` logo após clonar). Todos os caminhos são
ancorados ao diretório deste arquivo (ROOT), nunca ao cwd — assim o comando
funciona chamado de qualquer lugar, inclusive via symlink no PATH.
"""
from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import env_file
import local_models
import ollama_ctl
from cli_select import confirm, select_menu

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
RUN_PY = ROOT / "run.py"
DATA_DIR = ROOT / "data"
PID_FILE = DATA_DIR / "server.pid"
LOG_FILE = DATA_DIR / "server.log"

IS_WIN = os.name == "nt"

# Campos essenciais perguntados no `setup` (chave, default, descrição, segredo?)
CORE_FIELDS = [
    ("GEMINI_API_KEY", "", "Chave da API do Google Gemini (obrigatória) — pegue em https://ai.google.dev/", True),
    ("JWT_SECRET_KEY", "", "Segredo para assinar tokens JWT (gerado automaticamente se vazio)", True),
    ("HELENA_PORT", "5000", "Porta HTTP do servidor", False),
    ("HELENA_HOST", "0.0.0.0", "Interface de bind (0.0.0.0 = acessível na rede)", False),
]
# Campos avançados (só perguntados com --advanced; têm default sensato)
ADVANCED_FIELDS = [
    ("GEMINI_MODEL", "gemini-2.5-flash", "Modelo do agente"),
    ("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image", "Modelo de geração de imagem"),
    ("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts", "Modelo de TTS (voz)"),
    ("GEMINI_TTS_VOICE", "Kore", "Voz do TTS"),
    ("TELEGRAM_BOT_TOKEN", "", "Token do bot do Telegram (@BotFather) — vazio desliga o bot"),
]
SECRET_KEYS = {"GEMINI_API_KEY", "JWT_SECRET_KEY", "TELEGRAM_BOT_TOKEN"}
JWT_PLACEHOLDER = "change-me-to-a-long-random-string"

# ---------- saída colorida ----------
_TTY = sys.stdout.isatty()
def _c(txt: str, code: str) -> str:
    return f"\033[{code}m{txt}\033[0m" if _TTY else txt
def ok(t): return _c(t, "32")
def warn(t): return _c(t, "33")
def err(t): return _c(t, "31")
def dim(t): return _c(t, "2")
def bold(t): return _c(t, "1")


# ---------- decoração (banner, cabeçalhos de seção, spinner) ----------
def _box(lines: list[str], color_code: str = "35") -> str:
    """Caixa com bordas — a largura é calculada ANTES de colorir (um código
    ANSI conta como caractere pro len(), coloria por linha desalinharia)."""
    width = max(len(line) for line in lines) + 2
    top = "╭" + "─" * width + "╮"
    bottom = "╰" + "─" * width + "╯"
    middle = [f"│ {line.ljust(width - 1)}│" for line in lines]
    body = "\n".join([top, *middle, bottom])
    return _c(body, color_code) if _TTY else body


def _banner() -> str:
    return _box([
        "H E L E N A",
        "assistente pessoal — Gemini ou modelo local",
    ])


def _section(title: str) -> None:
    """Cabeçalho de uma seção com várias linhas (doctor, models list, etc.)
    — uma linha só, não compete por espaço vertical com o conteúdo."""
    filler = "─" * max(2, 44 - len(title))
    print(bold(f"── {title} {filler}"))


_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class _Spinner:
    """Spinner de terminal pra esperas com polling (health check, etc.).
    Sem TTY (log/pipe), vira só uma linha estática — nunca spamma um pipe
    com \\r repetido."""

    def __init__(self, label: str):
        self.label = label
        self._frame = 0
        if not _TTY:
            print(dim(f"{label}..."))

    def tick(self) -> None:
        if not _TTY:
            return
        frame = _SPINNER_FRAMES[self._frame % len(_SPINNER_FRAMES)]
        sys.stdout.write(f"\r{_c(frame, '36')} {self.label}...")
        sys.stdout.flush()
        self._frame += 1

    def stop(self, final_line: str) -> None:
        if _TTY:
            sys.stdout.write("\r" + " " * (len(self.label) + 14) + "\r")
        print(final_line)


# ---------- gerência do .env ----------
# implementação real em env_file.py (compartilhada com o blueprint web) —
# aqui só amarra ao caminho ENV/ENV_EXAMPLE deste processo
def read_env_values() -> dict[str, str]:
    """Lê os pares KEY=VALUE ativos (não comentados) do .env."""
    return env_file.read_env_values(ENV)


def set_env_values(updates: dict[str, str]) -> None:
    """Atualiza (ou insere) chaves no .env preservando comentários e ordem."""
    env_file.set_env_values(ENV, updates, example_path=ENV_EXAMPLE)


def mask(value: str) -> str:
    if not value:
        return dim("(vazio)")
    return env_file.mask_plain(value)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def env_port() -> int:
    return int(read_env_values().get("HELENA_PORT") or os.environ.get("HELENA_PORT") or 5000)


def _db_path() -> Path:
    """Caminho do SQLite (honra DATABASE_URL/HELENA_DATA_DIR; senão data/helena.db)."""
    vals = read_env_values()
    url = vals.get("DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        p = url[len("sqlite:///"):]
        return Path(p) if os.path.isabs(p) else (ROOT / p)
    data = vals.get("HELENA_DATA_DIR") or "data"
    base = Path(data) if os.path.isabs(data) else (ROOT / data)
    return base / "helena.db"


# ---------- processo do servidor ----------
def _server_python() -> str:
    """Python do venv (com as deps do app). Funciona quer o cli tenha sido
    chamado por `uv run python cli.py` quer por `python cli.py`."""
    venv_py = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(venv_py) if venv_py.exists() else sys.executable


def _pid_alive(pid: int) -> bool:
    """Testa se o processo existe SEM matá-lo (multiplataforma).
    No Windows `os.kill(pid, 0)` mataria o processo — usa tasklist lá."""
    if IS_WIN:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True,
        )
        return str(pid) in r.stdout
    try:
        os.kill(pid, 0)  # sinal 0: só testa existência (POSIX)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe, mas de outro dono


def running_pid() -> int | None:
    """PID vivo do servidor, ou None. Limpa pidfile obsoleto."""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        PID_FILE.unlink(missing_ok=True)
        return None
    if _pid_alive(pid):
        return pid
    PID_FILE.unlink(missing_ok=True)
    return None


def health_ok(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


# ---------- comandos ----------
def _setup_local_llm(updates: dict) -> bool:
    """Fluxo de configuração do modelo local (Ollama): instala se faltar,
    detecta hardware, mostra o catálogo colorido, baixa o modelo escolhido.
    Preenche `updates` e devolve True em caso de sucesso (False = caiu pro
    Gemini, seja por cancelamento ou por falha de instalação)."""
    if not _ensure_ollama_installed():
        print(warn("Sem o Ollama instalado, não dá pra usar modelo local agora."))
        return False

    host = OLLAMA_DEFAULT_HOST
    if not _ensure_ollama_daemon(host):
        print(warn("Não consegui confirmar que o Ollama está respondendo — seguindo mesmo assim."))

    hw = local_models.detect_hardware()
    gpu = f"{hw['gpu_vram_gb']}GB VRAM" if hw.get("gpu_vram_gb") else "sem GPU detectada (CPU)"
    print(dim(f"\nHardware detectado: {hw['ram_gb']}GB RAM, {hw['cpu_count']} CPUs, {gpu}"))

    installed = _ollama_list_installed()
    options = _catalog_options(hw, installed=installed)
    options.append(("__custom__", "(digitar outro nome de modelo)"))
    chosen = select_menu("\nQual modelo usar?", options)
    if chosen is None:
        print(warn("cancelado."))
        return False
    if chosen == "__custom__":
        try:
            chosen = input("Nome do modelo (tag do 'ollama pull', ex.: qwen2.5:7b): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if not chosen:
            print(warn("cancelado."))
            return False

    if chosen not in installed and not _ollama_pull(chosen):
        print(err(f"não consegui baixar {chosen} — tente depois com 'helena models pull'."))
        return False

    print(dim("testando se o modelo roda de verdade (não só se baixou)..."))
    smoke_ok, smoke_detail = _ollama_smoke_test(host, chosen)
    if not smoke_ok:
        print(err(f"✗ o modelo baixou mas não RODA: {smoke_detail}"))
        print(warn(
            "Isso costuma ser uma instalação incompleta/quebrada do Ollama "
            "(ex.: binário de execução ausente ou sem permissão), não um "
            "problema da Helena. Tente reinstalar o Ollama:"
        ))
        print(dim("  curl -fsSL https://ollama.com/install.sh | sh"))
        print(warn("Configuração local NÃO foi salva — mantendo Gemini."))
        return False
    print(ok("✓ modelo respondeu normalmente."))

    updates["LLM_PROVIDER"] = "ollama"
    updates["OLLAMA_MODEL"] = chosen
    updates["OLLAMA_HOST"] = host
    updates["OLLAMA_MANAGED"] = "1"
    print(ok(f"\n✓ modelo local configurado: {chosen}"))
    print(warn(
        "⚠ geração de imagem, TTS e descrição de fotos/áudio enviados exigem "
        "Gemini — ficam indisponíveis a menos que você TAMBÉM configure "
        "GEMINI_API_KEY (helena config set GEMINI_API_KEY sua-chave)."
    ))
    return True


def cmd_setup(args) -> int:
    print(bold("Configuração da Helena"))
    print(dim("Enter mantém o valor atual/default entre colchetes.\n"))
    current = read_env_values()
    updates: dict[str, str] = {}

    provider = current.get("LLM_PROVIDER", "gemini")
    if _interactive():
        picked = select_menu(
            "Qual cérebro a Helena vai usar?",
            [
                ("gemini", "Gemini (nuvem, precisa de chave de API)"),
                ("local", "Modelo local (Ollama, roda na sua máquina)"),
            ],
            default=1 if provider == "ollama" else 0,
        )
        if picked is not None:
            provider = picked

    using_ollama = provider in ("local", "ollama") and _setup_local_llm(updates)
    if using_ollama:
        updates["LLM_PROVIDER"] = "ollama"
    else:
        updates["LLM_PROVIDER"] = "gemini"

    for key, default, desc, secret in CORE_FIELDS:
        if key == "GEMINI_API_KEY" and using_ollama:
            continue  # modo local não exige Gemini
        cur = current.get(key, "")
        if key == "JWT_SECRET_KEY":
            # gera só se faltar ou for o placeholder — nunca regenera (deslogaria todos)
            if not cur or cur == JWT_PLACEHOLDER:
                updates[key] = secrets.token_urlsafe(48)
                print(f"{ok('✓')} {key}: segredo novo gerado automaticamente")
            else:
                print(f"{dim('·')} {key}: mantido (já configurado)")
            continue
        shown = mask(cur) if secret else (cur or dim(f"default: {default}"))
        try:
            ans = input(f"{bold(key)} [{shown}]\n  {dim(desc)}\n  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n" + warn("cancelado."))
            return 1
        if ans:
            updates[key] = ans
        elif not cur and default:
            updates[key] = default

    if args.advanced:
        print(dim("\n-- avançado --"))
        for key, default, desc in ADVANCED_FIELDS:
            cur = current.get(key, "")
            ans = input(f"{bold(key)} [{cur or default}]\n  {dim(desc)}\n  > ").strip()
            updates[key] = ans or cur or default

    set_env_values(updates)
    print(ok(f"\n✓ Configuração salva em {ENV}"))
    final = read_env_values()
    if final.get("LLM_PROVIDER") == "ollama":
        if not final.get("OLLAMA_MODEL"):
            print(warn("⚠ nenhum modelo local escolhido ainda — rode 'helena models use' antes de iniciar."))
    elif not final.get("GEMINI_API_KEY"):
        print(warn("⚠ GEMINI_API_KEY ainda está vazia — a IA não vai funcionar sem ela."))
    print(dim("Rode 'helena start' para iniciar."))
    return 0


_KNOWN_ENV_KEYS = (
    [k for k, *_ in CORE_FIELDS] + [k for k, *_ in ADVANCED_FIELDS]
    + ["LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL", "OLLAMA_MANAGED"]
)


def _pick_env_key(prompt: str) -> str | None:
    """Seleciona uma chave conhecida do .env, ou digite uma nova (via 'Outra...')."""
    known = _KNOWN_ENV_KEYS + [k for k in read_env_values() if k not in _KNOWN_ENV_KEYS]
    options = [(k, k) for k in known] + [("__custom__", "(digitar outra chave)")]
    picked = select_menu(prompt, options)
    if picked is None:
        return None
    if picked != "__custom__":
        return picked
    try:
        return input("Chave (ex.: GEMINI_API_KEY): ").strip().upper() or None
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def cmd_config(args) -> int:
    action = args.action
    if action is None:
        if not _interactive():
            print(err("uso: helena config {list|get|set} [CHAVE] [VALOR]"))
            return 2
        action = select_menu(
            "O que você quer fazer?",
            [("list", "listar todas"), ("get", "ler uma chave"), ("set", "gravar uma chave")],
        )
        if action is None:
            print(warn("cancelado."))
            return 1

    if action == "list":
        vals = read_env_values()
        if not vals:
            print(dim("Nenhuma variável configurada (rode 'helena setup')."))
            return 0
        _section("Configuração (.env)")
        for key, val in vals.items():
            shown = mask(val) if key in SECRET_KEYS else val
            print(f"  {bold(key)}={shown}")
        return 0
    if action == "get":
        key = args.key
        if not key and _interactive():
            key = _pick_env_key("Qual chave?")
        if not key:
            print(err("uso: helena config get CHAVE"))
            return 2
        val = read_env_values().get(key)
        if val is None:
            print(dim(f"{key} não configurada"))
            return 1
        print(val)
        return 0
    if action == "set":
        key = args.key
        if not key and _interactive():
            key = _pick_env_key("Qual chave?")
        value = args.value
        if key and value is None and _interactive():
            try:
                value = input(f"Novo valor para {bold(key)}: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 1
        if not key or value is None:
            print(err("uso: helena config set CHAVE VALOR"))
            return 2
        set_env_values({key: value})
        print(ok(f"✓ {key} atualizado"))
        return 0
    return 2


def cmd_start(args) -> int:
    if service_installed():  # fonte única da verdade: delega ao serviço
        _service_action("start")
        print(ok("✓ serviço iniciado"))
        return _wait_health()
    pid = running_pid()
    if pid:
        print(warn(f"Já está rodando (pid {pid})."))
        return 0
    if not ENV.exists():
        print(warn("Sem .env — rode 'helena setup' primeiro (ou 'install.sh')."))
        return 1
    if not read_env_values().get("GEMINI_API_KEY"):
        print(warn("⚠ GEMINI_API_KEY vazia; iniciando mesmo assim (a IA não responderá)."))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = env_port()
    logf = open(LOG_FILE, "ab")
    logf.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n".encode())
    logf.flush()
    # desacopla o servidor do terminal do CLI (sobrevive ao fechar o shell)
    spawn_kwargs: dict = dict(cwd=str(ROOT), stdout=logf, stderr=subprocess.STDOUT)
    if IS_WIN:
        spawn_kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    else:
        spawn_kwargs["start_new_session"] = True
    proc = subprocess.Popen([_server_python(), str(RUN_PY)], **spawn_kwargs)
    PID_FILE.write_text(str(proc.pid))
    spin = _Spinner(f"iniciando (pid {proc.pid}) na porta {port}")
    for _ in range(40):  # ~20s
        spin.tick()
        if proc.poll() is not None:
            spin.stop(err("✗ o servidor saiu logo ao subir. Últimas linhas do log:"))
            _print_log_tail(20)
            PID_FILE.unlink(missing_ok=True)
            return 1
        if health_ok(port):
            spin.stop(ok(f"✓ rodando em http://localhost:{port}  (pid {proc.pid})"))
            return 0
        time.sleep(0.5)
    spin.stop(warn("Subiu mas /health ainda não respondeu. Veja 'helena logs'."))
    return 0


def _kill_tree(pid: int, force: bool) -> None:
    """Encerra o servidor e seus filhos, multiplataforma."""
    if IS_WIN:
        # taskkill /T encerra a árvore; /F força
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []),
            capture_output=True,
        )
        return
    try:
        sig = signal.SIGKILL if force else signal.SIGTERM
        os.killpg(os.getpgid(pid), sig)  # mata o grupo (server + threads)
    except ProcessLookupError:
        pass


def _stop_pidfile() -> bool:
    """Para o servidor em background (pidfile). True se havia algo pra parar."""
    pid = running_pid()
    if not pid:
        return False
    _kill_tree(pid, force=False)
    for _ in range(20):  # espera até 10s o encerramento gracioso
        if running_pid() is None:
            break
        time.sleep(0.5)
    if running_pid() is not None:
        _kill_tree(pid, force=True)
    PID_FILE.unlink(missing_ok=True)
    return True


def cmd_stop(args) -> int:
    if service_installed():  # delega ao serviço
        _service_action("stop")
        print(ok("✓ serviço parado."))
        return 0
    if _stop_pidfile():
        print(ok("✓ parado."))
    else:
        print(dim("Não está rodando."))
    return 0


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(1)
    return cmd_start(args)


def cmd_status(args) -> int:
    if service_installed():  # fonte única da verdade
        return _service_status()
    pid = running_pid()
    port = env_port()
    healthy = health_ok(port) if pid else False
    if pid:
        print(f"servidor:  {ok('rodando')}  (pid {pid})")
    else:
        print(f"servidor:  {dim('parado')}")
    if pid:
        print(f"saúde:     {ok('/health ok') if healthy else warn('não responde ainda')}")
        print(f"url:       http://localhost:{port}")
    _print_llm_status()
    return 0 if (pid and healthy) else (0 if not pid else 1)


def _print_log_tail(n: int) -> None:
    if not LOG_FILE.exists():
        print(dim("(sem log ainda)"))
        return
    lines = LOG_FILE.read_text(errors="replace").splitlines()
    for line in lines[-n:]:
        print(dim("  " + line))


def cmd_logs(args) -> int:
    if not LOG_FILE.exists():
        print(dim("Sem log ainda (o servidor nunca foi iniciado)."))
        return 0
    if args.follow:
        try:
            os.execvp("tail", ["tail", "-n", "60", "-f", str(LOG_FILE)])
        except OSError:
            print(err("'tail' indisponível; mostrando as últimas linhas:"))
    _print_log_tail(args.lines)
    return 0


def _git(*a) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *a], cwd=str(ROOT), capture_output=True, text=True)


def _apply_restart() -> None:
    """Reinicia o servidor pra carregar o código novo (serviço ou pidfile)."""
    if service_installed():
        _service_action("restart")
        print(ok("✓ serviço reiniciado (código novo aplicado)."))
    elif running_pid():
        cmd_restart(None)
    else:
        print(dim("Rode 'helena start' para aplicar."))


def _update_code() -> int:
    """Aplica mudanças que VOCÊ fez no código local: re-sincroniza deps e reinicia.
    (Não mexe no git — serve pra árvore com alterações locais.)"""
    print(dim("sincronizando dependências (uv sync)..."))
    if subprocess.run(["uv", "sync"], cwd=str(ROOT)).returncode != 0:
        print(warn("'uv sync' falhou. Rode manualmente."))
        return 1
    _apply_restart()
    print(ok("✓ código local aplicado."))
    return 0


def _update_git() -> int:
    if not (ROOT / ".git").exists():
        print(warn("Isto não é um clone git (baixado como zip?). Atualize baixando a versão nova."))
        return 1
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print(err("git indisponível ou repositório inválido."))
        return 1
    dirty = _git("status", "--porcelain").stdout.strip()
    if dirty:
        print(warn("Há mudanças locais não commitadas — use 'helena update code' para aplicá-las,"))
        print(warn("ou commite/descarte antes de puxar do git:"))
        print(dim(dirty))
        return 1
    up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if up.returncode != 0:
        print(warn("Nenhum branch remoto configurado (upstream). Nada a atualizar."))
        return 1
    print(dim("buscando atualizações..."))
    if _git("fetch", "--quiet").returncode != 0:
        print(err("falha no 'git fetch' (sem internet?)."))
        return 1
    behind = _git("rev-list", "--count", "HEAD..@{u}").stdout.strip() or "0"
    if behind == "0":
        print(ok("✓ já está na versão mais recente."))
        return 0
    print(f"{behind} atualização(ões) disponível(is). Baixando...")
    pull = _git("pull", "--ff-only")
    if pull.returncode != 0:
        print(err("falha no 'git pull --ff-only':"))
        print(dim(pull.stderr or pull.stdout))
        return 1
    print(dim("sincronizando dependências (uv sync)..."))
    if subprocess.run(["uv", "sync"], cwd=str(ROOT)).returncode != 0:
        print(warn("git ok, mas 'uv sync' falhou. Rode 'uv sync' manualmente."))
        return 1
    _apply_restart()  # só chega aqui se houve mudança (behind != 0)
    print(ok("✓ atualizado."))
    return 0


def cmd_update(args) -> int:
    mode = getattr(args, "action", None)
    if mode is None:
        if _interactive():
            mode = select_menu(
                "Como atualizar?",
                [("git", "puxar do remoto (git pull)"), ("code", "aplicar mudanças locais")],
            )
            if mode is None:
                print(warn("cancelado."))
                return 1
        else:
            mode = "git"
    return _update_code() if mode == "code" else _update_git()


# ---------- modelos locais (Ollama) ----------
# implementação real em ollama_ctl.py (compartilhada com o blueprint web) —
# aqui só amarra à apresentação no terminal (print colorido)
OLLAMA_DEFAULT_HOST = ollama_ctl.DEFAULT_HOST
_RATING_LABEL = {"green": "adequado", "yellow": "roda, mas custa desempenho", "red": "não recomendado pra essa máquina"}
_RATING_COLOR = {"green": ok, "yellow": warn, "red": err}


def _ollama_reachable(host: str, timeout: float = 2.0) -> bool:
    return ollama_ctl.reachable(host, timeout)


def _ollama_list_installed() -> set[str]:
    return ollama_ctl.list_installed()


def _ollama_pull(name: str) -> bool:
    print(dim(f"baixando {name} (pode demorar um pouco)..."))
    if ollama_ctl.pull(name):
        return True
    print(err("falha ao rodar 'ollama pull'."))
    return False


def _ollama_smoke_test(host: str, model: str, timeout: float = 90.0) -> tuple[bool, str]:
    return ollama_ctl.smoke_test(host, model, timeout)


def _ollama_rm(name: str) -> bool:
    ok_, msg = ollama_ctl.rm(name)
    if not ok_:
        print(err(msg))
    return ok_


def _ensure_ollama_daemon(host: str) -> bool:
    if not ollama_ctl.reachable(host):
        print(dim("subindo o daemon do Ollama..."))
    return ollama_ctl.ensure_daemon(host)


def _ensure_ollama_installed() -> bool:
    if shutil.which("ollama"):
        return True
    print(warn("Ollama não encontrado nesta máquina."))
    if IS_WIN:
        print(dim("Baixe e instale em: https://ollama.com/download/windows"))
        print(dim("Depois rode 'helena setup' de novo."))
        return False
    if not confirm("Instalar o Ollama agora (instalador oficial, curl | sh)?", default=True):
        return False
    print(dim("instalando o Ollama..."))
    ok_, msg = ollama_ctl.install()
    if not ok_:
        print(err(msg))
        return False
    print(ok("✓ Ollama instalado."))
    return True


def _describe_model(m: dict, hw: dict, installed: set[str] = frozenset(), active: str | None = None) -> str:
    rating = local_models.rate_model(m, hw)
    tag = _RATING_COLOR[rating](f"[{_RATING_LABEL[rating]}]")
    marks = []
    if m["name"] == active:
        marks.append(ok("ativo"))
    if m["name"] in installed:
        marks.append(dim("baixado"))
    suffix = f"  ({', '.join(marks)})" if marks else ""
    return f"{m['name']:<20} {m['params_b']:>5}B  ~{m['est_gb']:>5.1f}GB  {tag}{suffix}"


def _catalog_options(hw: dict, installed: set[str] = frozenset(), active: str | None = None):
    return [(m["name"], _describe_model(m, hw, installed, active)) for m in local_models.CATALOG]


def _print_llm_status() -> None:
    vals = read_env_values()
    provider = vals.get("LLM_PROVIDER", "gemini")
    if provider == "ollama":
        host = vals.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST
        model = vals.get("OLLAMA_MODEL") or dim("(nenhum — rode 'helena models use')")
        reach = ok("respondendo") if _ollama_reachable(host) else dim("não respondendo")
        print(f"IA:        local (ollama) — modelo {model} — {reach}")
    else:
        print("IA:        gemini")


def cmd_doctor(args) -> int:
    def line(good, label, extra=""):
        mark = ok("✓") if good else err("✗")
        print(f"  {mark} {label}{('  ' + dim(extra)) if extra else ''}")

    _section("Diagnóstico")
    line(bool(shutil.which("uv")), "uv instalado", "" if shutil.which("uv") else "instale: https://astral.sh/uv")
    venv_py = Path(_server_python())
    line(venv_py.exists() and ".venv" in str(venv_py), "ambiente (.venv) criado", "" if venv_py.exists() else "rode 'uv sync'")
    line(ENV.exists(), ".env presente", "" if ENV.exists() else "rode 'helena setup'")
    vals = read_env_values()
    provider = vals.get("LLM_PROVIDER", "gemini")
    if provider == "ollama":
        model = vals.get("OLLAMA_MODEL", "")
        host = vals.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST
        line(bool(model), f"modelo local configurado ({model or 'nenhum'})", "" if model else "rode 'helena models use'")
        reachable = _ollama_reachable(host)
        line(reachable, f"Ollama respondendo em {host}", "" if reachable else "'helena start' sobe junto, ou rode 'ollama serve'")
        if reachable and model:
            smoke_ok, smoke_detail = _ollama_smoke_test(host, model, timeout=30)
            line(smoke_ok, f"modelo '{model}' roda de verdade (teste de geração)",
                 "" if smoke_ok else f"{smoke_detail} — provável instalação quebrada do Ollama; tente reinstalar")
    else:
        line(bool(vals.get("GEMINI_API_KEY")), "GEMINI_API_KEY configurada")
    jwt = vals.get("JWT_SECRET_KEY", "")
    line(bool(jwt) and jwt != JWT_PLACEHOLDER, "JWT_SECRET_KEY configurada")
    pid = running_pid()
    port = env_port()
    line(bool(pid), "servidor rodando", f"pid {pid}" if pid else "helena start")
    if pid:
        line(health_ok(port), f"/health responde na porta {port}")
    return 0


def cmd_models(args) -> int:
    """Gerencia modelos locais (Ollama): listar catálogo, trocar/baixar/remover."""
    action = args.action
    if action is None:
        if not _interactive():
            print(err("uso: helena models {list|use|pull|remove} [nome]"))
            return 2
        action = select_menu(
            "O que fazer com os modelos locais?",
            [
                ("list", "listar catálogo (com recomendação pro seu hardware)"),
                ("use", "trocar o modelo ativo"),
                ("pull", "baixar um modelo"),
                ("remove", "remover um modelo já baixado"),
            ],
        )
        if action is None:
            print(warn("cancelado."))
            return 1

    if action == "list":
        _section("Modelos locais (Ollama)")
        hw = local_models.detect_hardware()
        installed = _ollama_list_installed()
        active = read_env_values().get("OLLAMA_MODEL")
        gpu = f"{hw['gpu_vram_gb']}GB VRAM" if hw.get("gpu_vram_gb") else "sem GPU detectada (CPU)"
        print(bold(f"Hardware: {hw['ram_gb']}GB RAM, {hw['cpu_count']} CPUs, {gpu}\n"))
        for m in local_models.CATALOG:
            print("  " + _describe_model(m, hw, installed, active))
        return 0

    if not shutil.which("ollama"):
        print(err("Ollama não instalado — rode 'helena setup' e escolha modelo local."))
        return 1

    name = args.name
    host = read_env_values().get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST

    if action == "use":
        if not name and _interactive():
            hw = local_models.detect_hardware()
            installed = _ollama_list_installed()
            active = read_env_values().get("OLLAMA_MODEL")
            name = select_menu("Qual modelo usar?", _catalog_options(hw, installed, active))
        if not name:
            print(err("uso: helena models use <nome>"))
            return 2
        if name not in _ollama_list_installed() and not _ollama_pull(name):
            print(err(f"não consegui baixar {name}."))
            return 1
        _ensure_ollama_daemon(host)
        smoke_ok, smoke_detail = _ollama_smoke_test(host, name)
        if not smoke_ok:
            print(err(f"✗ o modelo baixou mas não RODA: {smoke_detail}"))
            print(warn("provável instalação quebrada do Ollama — veja 'helena doctor' ou reinstale:"))
            print(dim("  curl -fsSL https://ollama.com/install.sh | sh"))
            return 1
        set_env_values({"OLLAMA_MODEL": name})
        print(ok(f"✓ modelo ativo: {name} (testado e respondendo)"))
        return 0

    if action == "pull":
        if not name and _interactive():
            hw = local_models.detect_hardware()
            installed = _ollama_list_installed()
            name = select_menu("Qual modelo baixar?", _catalog_options(hw, installed))
        if not name:
            print(err("uso: helena models pull <nome>"))
            return 2
        if not _ollama_pull(name):
            return 1
        _ensure_ollama_daemon(host)
        smoke_ok, smoke_detail = _ollama_smoke_test(host, name)
        if not smoke_ok:
            print(err(f"✗ baixou, mas o modelo não RODA: {smoke_detail}"))
            print(warn("provável instalação quebrada do Ollama — veja 'helena doctor' ou reinstale:"))
            print(dim("  curl -fsSL https://ollama.com/install.sh | sh"))
            return 1
        print(ok("✓ baixado e testado — respondeu normalmente."))
        return 0

    if action == "remove":
        installed = _ollama_list_installed()
        if not name and _interactive():
            if not installed:
                print(dim("Nenhum modelo baixado."))
                return 0
            name = select_menu("Qual modelo remover?", [(n, n) for n in sorted(installed)])
        if not name:
            print(err("uso: helena models remove <nome>"))
            return 2
        if _ollama_rm(name):
            print(ok(f"✓ {name} removido."))
            return 0
        return 1

    return 2


def _restart_hint() -> None:
    if running_pid() or (service_installed() and _service_active()):
        print(dim("Reinicie pra aplicar: helena restart"))
    else:
        print(dim("Vale rodar 'helena start' quando quiser usar."))


def cmd_provider(args) -> int:
    """Troca o cérebro da Helena entre Gemini (nuvem) e Ollama (local), com
    as checagens que 'helena config set LLM_PROVIDER ...' sozinho não faz:
    valida que o destino realmente tem como funcionar antes de gravar."""
    vals = read_env_values()
    current = vals.get("LLM_PROVIDER", "gemini")
    target = args.name

    if target is None:
        cur_label = "local (ollama)" if current == "ollama" else "gemini"
        print(f"Cérebro atual: {bold(cur_label)}")
        if not _interactive():
            print(dim("uso: helena provider {gemini|ollama}"))
            return 0
        target = select_menu(
            "Trocar para qual cérebro?",
            [
                ("gemini", "Gemini (nuvem)" + (dim("  — atual") if current != "ollama" else "")),
                ("ollama", "Modelo local (Ollama)" + (dim("  — atual") if current == "ollama" else "")),
            ],
            default=1 if current == "ollama" else 0,
        )
        if target is None:
            print(warn("cancelado."))
            return 1

    if target not in ("gemini", "ollama"):
        print(err("uso: helena provider {gemini|ollama}"))
        return 2

    if target == current:
        print(dim(f"já está em '{target}' — nada a fazer."))
        return 0

    if target == "gemini":
        set_env_values({"LLM_PROVIDER": "gemini"})
        print(ok("✓ cérebro trocado para Gemini."))
        if not vals.get("GEMINI_API_KEY"):
            print(warn("⚠ GEMINI_API_KEY vazia — configure com 'helena config set GEMINI_API_KEY sua-chave'."))
        _restart_hint()
        return 0

    # target == "ollama"
    model = vals.get("OLLAMA_MODEL")
    if not model:
        print(warn("Nenhum modelo local configurado ainda."))
        if not shutil.which("ollama"):
            print(err("Ollama não está instalado — rode 'helena setup' e escolha modelo local."))
            return 1
        if not _interactive():
            print(err("rode 'helena models use <nome>' primeiro, ou 'helena provider' num terminal interativo."))
            return 1
        hw = local_models.detect_hardware()
        installed = _ollama_list_installed()
        model = select_menu("Qual modelo usar?", _catalog_options(hw, installed))
        if model is None:
            print(warn("cancelado."))
            return 1
        host = vals.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST
        if model not in installed and not _ollama_pull(model):
            print(err(f"não consegui baixar {model}."))
            return 1
        _ensure_ollama_daemon(host)
        smoke_ok, smoke_detail = _ollama_smoke_test(host, model)
        if not smoke_ok:
            print(err(f"✗ o modelo baixou mas não RODA: {smoke_detail}"))
            print(warn("provável instalação quebrada do Ollama — veja 'helena doctor' ou reinstale:"))
            print(dim("  curl -fsSL https://ollama.com/install.sh | sh"))
            return 1

    set_env_values({
        "LLM_PROVIDER": "ollama",
        "OLLAMA_MODEL": model,
        "OLLAMA_HOST": vals.get("OLLAMA_HOST") or OLLAMA_DEFAULT_HOST,
        "OLLAMA_MANAGED": vals.get("OLLAMA_MANAGED", "1"),
    })
    print(ok(f"✓ cérebro trocado para modelo local ({model})."))
    _restart_hint()
    return 0


def _find_user_row(con: sqlite3.Connection, identifier: str):
    """Busca por email primeiro (identificador principal agora); cai pra
    username (contas antigas / bookkeeping interno gerado no registro)."""
    row = con.execute("SELECT id, username FROM users WHERE email=?", (identifier,)).fetchone()
    if row is None:
        row = con.execute("SELECT id, username FROM users WHERE username=?", (identifier,)).fetchone()
    return row


def _list_user_rows(con: sqlite3.Connection):
    return con.execute(
        "SELECT id, username, email, name, is_principal, shell_full_control, "
        "sudo_enabled, sudo_require_approval FROM users ORDER BY id"
    ).fetchall()


def _pick_user_identifier(con: sqlite3.Connection, prompt: str) -> str | None:
    """Seleciona um usuário existente (mostra email, cai pro username se faltar)."""
    rows = _list_user_rows(con)
    if not rows:
        return None
    options = []
    for uid, uname, email, name, *_ in rows:
        ident = email or uname
        label = f"{email or uname}" + (f"  [{name}]" if name else "")
        options.append((ident, label))
    return select_menu(prompt, options)


def cmd_users(args) -> int:
    """Lista usuários e define permissão/email."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado — rode o servidor ao menos uma vez ('helena start')."))
        return 1
    con = sqlite3.connect(str(dbp))
    con.execute("PRAGMA busy_timeout=5000")
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if not {"is_principal", "shell_full_control", "name", "sudo_enabled", "sudo_require_approval"} <= cols:
        print(warn("Colunas novas ainda não existem — reinicie o servidor ('helena restart') para migrar."))
        return 1

    action = args.action
    if action is None:
        if _interactive():
            action = select_menu(
                "O que você quer fazer?",
                [
                    ("list", "listar usuários"),
                    ("principal", "promover a principal"),
                    ("fullcontrol", "promover a controle absoluto"),
                    ("normal", "rebaixar a normal"),
                    ("sudo", "habilitar sudo"),
                    ("nosudo", "desabilitar sudo"),
                    ("email", "definir email de conta antiga"),
                ],
            )
            if action is None:
                print(warn("cancelado."))
                return 1
        else:
            action = "list"

    if action == "list":
        rows = _list_user_rows(con)
        if not rows:
            print(dim("Nenhum usuário cadastrado."))
            return 0
        _section("Usuários")
        for uid, uname, email, name, principal, full, sudo_en, sudo_appr in rows:
            label = email or f"{uname} (sem email — conta antiga)"
            if name:
                label = f"{label}  [{name}]"
            if full:
                tag = err("⚡ controle absoluto")
            elif principal:
                tag = ok("★ principal")
            else:
                tag = dim("normal")
            if sudo_en:
                tag += "  " + warn("🔓 sudo" + (" (sempre pede aprovação)" if sudo_appr else " (sem aprovação extra)"))
            print(f"  {uid:>3}  {label:<40} {tag}")
        print(dim("\nNíveis: normal → principal (com aprovação) → fullcontrol (sem aprovação)."))
        print(dim("Ex.: helena users principal <email> | helena users fullcontrol <email> | helena users normal <email>"))
        print(dim("Sudo é À PARTE: helena users sudo <email> | helena users nosudo <email>"))
        print(dim("Conta antiga sem email? helena users email <username_antigo> <novo@email.com>"))
        return 0

    identifier = args.identifier
    if not identifier and _interactive():
        identifier = _pick_user_identifier(con, "Qual usuário?")

    if action == "email":
        value = args.value
        if identifier and value is None and _interactive():
            try:
                value = input("Novo email: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 1
        if not identifier or not value:
            print(err("uso: helena users email <email_ou_username_atual> <novo_email>"))
            return 2
        row = _find_user_row(con, identifier)
        if row is None:
            print(err(f"usuário '{identifier}' não encontrado (veja 'helena users')"))
            return 1
        uid, uname = row
        new_email = value.strip().lower()
        try:
            with con:
                con.execute("UPDATE users SET email=? WHERE id=?", (new_email, uid))
        except sqlite3.IntegrityError:
            print(err(f"email '{new_email}' já está em uso por outra conta."))
            return 1
        print(ok(f"✓ {uname} (id {uid}) agora tem email {new_email}."))
        return 0

    if not identifier:
        print(err(f"uso: helena users {action} <email_ou_username>"))
        return 2
    row = _find_user_row(con, identifier)
    if row is None:
        print(err(f"usuário '{identifier}' não encontrado (veja 'helena users')"))
        return 1
    uid, uname = row

    if action == "nosudo":
        with con:
            con.execute("UPDATE users SET sudo_enabled=0 WHERE id=?", (uid,))
        print(ok(f"✓ sudo desabilitado pra {uname}."))
        return 0

    if action == "sudo":
        require_approval = 1
        if _interactive():
            choice = select_menu(
                "Comandos com sudo sempre devem pedir aprovação no chat?",
                [
                    ("yes", "sim, sempre pedir aprovação (recomendado)"),
                    ("no", "não, seguir a regra normal de confiança"),
                ],
            )
            if choice is None:
                print(warn("cancelado."))
                return 1
            require_approval = 1 if choice == "yes" else 0
        with con:
            con.execute(
                "UPDATE users SET sudo_enabled=1, sudo_require_approval=? WHERE id=?",
                (require_approval, uid),
            )
        print(err(f"🔓 {uname} agora pode usar sudo na Helena."))
        if require_approval:
            print(dim("   Todo comando com sudo vai pedir aprovação no chat, mesmo em controle absoluto."))
        else:
            print(dim("   Comandos com sudo seguem a mesma regra de confiança do resto do shell."))
        print(dim("   Isso só controla o que a Helena TENTA — o sudoers/NOPASSWD do sistema decide o resto."))
        print(dim(f"   Reverta com: helena users nosudo {identifier}"))
        return 0

    principal_v, full_v = {"fullcontrol": (1, 1), "principal": (1, 0), "normal": (0, 0)}[action]
    with con:
        con.execute(
            "UPDATE users SET is_principal=?, shell_full_control=? WHERE id=?",
            (principal_v, full_v, uid),
        )
    if action == "fullcontrol":
        print(err(f"⚡ {uname} agora tem CONTROLE ABSOLUTO — a Helena roda QUALQUER comando SEM pedir aprovação."))
        print(dim("   Cada comando ainda aparece no chat depois de rodar. Reverta com: helena users principal|normal " + identifier))
    elif action == "principal":
        print(ok(f"✓ {uname} agora é PRINCIPAL — pode pedir comandos (com aprovação no chat)."))
    else:
        print(ok(f"✓ {uname} agora é usuário normal — não pode executar comandos."))
    return 0


def cmd_chat(args) -> int:
    """Chat em texto puro, login por email+senha. Import tardio: chat_cli usa
    requests, ao contrário do resto do cli.py (stdlib-only)."""
    try:
        import chat_cli
    except ImportError as e:
        print(err(f"dependência faltando ({e}) — rode dentro do venv: 'uv run python cli.py chat'"))
        return 1
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return chat_cli.run(args, DATA_DIR, f"http://127.0.0.1:{env_port()}")


def cmd_goal(args) -> int:
    """Dá um propósito à Helena: ela pesquisa, planeja e (aprovado o plano)
    implementa — mesmo login/sessão do 'helena chat'. Import tardio (usa
    requests, via chat_cli/goal_cli)."""
    try:
        import goal_cli
    except ImportError as e:
        print(err(f"dependência faltando ({e}) — rode dentro do venv: 'uv run python cli.py goal'"))
        return 1
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return goal_cli.run(args, DATA_DIR, f"http://127.0.0.1:{env_port()}")


# ---------- serviço do sistema (systemd user no Linux / tarefa no Windows) ----------
# De propósito é um serviço de USUÁRIO (não root/system): só assim ele enxerga a
# sessão gráfica — controle de desktop (tela/mouse) morre num serviço de sistema.
SERVICE_NAME = "helena"
_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"
_UNIT = _UNIT_DIR / f"{SERVICE_NAME}.service"
_UPD_SERVICE = _UNIT_DIR / f"{SERVICE_NAME}-update.service"
_UPD_TIMER = _UNIT_DIR / f"{SERVICE_NAME}-update.timer"
_WIN_TASK = "Helena"
_WIN_UPD_TASK = "HelenaUpdate"
_GFX_ENV = (
    "WAYLAND_DISPLAY", "DISPLAY", "XDG_RUNTIME_DIR", "XDG_CURRENT_DESKTOP",
    "HYPRLAND_INSTANCE_SIGNATURE", "YDOTOOL_SOCKET", "PATH",
)


def _systemctl(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def service_installed() -> bool:
    if IS_WIN:
        return subprocess.run(
            ["schtasks", "/Query", "/TN", _WIN_TASK], capture_output=True
        ).returncode == 0
    return _UNIT.exists()


def _service_active() -> bool:
    if IS_WIN:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", _WIN_TASK, "/FO", "LIST", "/V"],
            capture_output=True, text=True,
        )
        return "Running" in r.stdout
    return _systemctl("is-active", SERVICE_NAME).stdout.strip() == "active"


def _service_action(action: str) -> None:  # start | stop | restart
    if IS_WIN:
        if action in ("stop", "restart"):
            subprocess.run(["schtasks", "/End", "/TN", _WIN_TASK], capture_output=True)
        if action in ("start", "restart"):
            subprocess.run(["schtasks", "/Run", "/TN", _WIN_TASK], capture_output=True)
        return
    _systemctl(action, SERVICE_NAME)


def _wait_health() -> int:
    port = env_port()
    spin = _Spinner("aguardando /health")
    for _ in range(30):
        spin.tick()
        if health_ok(port):
            spin.stop(ok(f"✓ no ar em http://localhost:{port}"))
            return 0
        time.sleep(0.5)
    spin.stop(warn("iniciado, mas /health ainda não respondeu. Veja 'helena logs'."))
    return 0


def _server_python_win() -> str:
    return str(ROOT / ".venv" / "Scripts" / "pythonw.exe")


def _service_install() -> int:
    if not ENV.exists():
        print(warn("Configure antes: 'helena setup'."))
        return 1
    if IS_WIN:
        # Tarefa no LOGON (não Windows Service): serviço roda na Session 0, isolada
        # do desktop — mouse/tela não funcionariam. Tarefa no logon usa a sessão.
        tr = f'cmd /c cd /d "{ROOT}" && "{_server_python_win()}" "{RUN_PY}"'
        r = subprocess.run(
            ["schtasks", "/Create", "/TN", _WIN_TASK, "/SC", "ONLOGON",
             "/RL", "LIMITED", "/F", "/TR", tr],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            print(err("falha ao criar a tarefa:")); print(dim(r.stderr)); return 1
        subprocess.run(["schtasks", "/Run", "/TN", _WIN_TASK], capture_output=True)
        print(ok("✓ instalado como tarefa de logon (roda ao entrar no Windows)."))
        return 0

    _stop_pidfile()  # evita conflito de porta com um server em background
    _UNIT_DIR.mkdir(parents=True, exist_ok=True)
    py = _server_python()
    # snapshot do ambiente gráfico (frágil, mas estável num desktop single-user)
    env_lines = "\n".join(
        f'Environment="{k}={os.environ[k]}"' for k in _GFX_ENV if os.environ.get(k)
    )
    _UNIT.write_text(
        "[Unit]\n"
        "Description=Helena — servidor da assistente pessoal\n"
        "After=graphical-session.target\n"
        "PartOf=graphical-session.target\n"
        "Wants=graphical-session.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={ROOT}\n"
        f"ExecStart={py} {RUN_PY}\n"
        "Restart=on-failure\n"
        "RestartSec=3\n"
        f"{env_lines}\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    _systemctl("daemon-reload")
    # backup do snapshot: injeta o env gráfico no manager de usuário
    subprocess.run(
        ["systemctl", "--user", "import-environment", *[k for k in _GFX_ENV if k != "PATH"]],
        capture_output=True,
    )
    r = _systemctl("enable", "--now", SERVICE_NAME)
    if r.returncode != 0:
        print(err("falha ao habilitar o serviço:")); print(dim(r.stderr)); return 1
    print(ok(f"✓ serviço instalado e rodando (systemd user: {SERVICE_NAME})."))
    print(dim("Sobe sozinho ao logar. IMPORTANTE: o controle de desktop (tela/mouse) só"))
    print(dim("dá pra confirmar após um LOGOUT/LOGIN real — não só agora."))
    return _wait_health()


def _service_uninstall() -> int:
    if IS_WIN:
        subprocess.run(["schtasks", "/Delete", "/TN", _WIN_TASK, "/F"], capture_output=True)
        print(ok("✓ tarefa removida.")); return 0
    _systemctl("disable", "--now", SERVICE_NAME)
    _UNIT.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    print(ok("✓ serviço removido.")); return 0


def _service_status() -> int:
    if not service_installed():
        print(dim("Serviço não instalado (rode 'helena service install')."))
        return 0
    active = _service_active()
    print(f"serviço:   {ok('rodando') if active else dim('parado')}"
          f"  ({'tarefa de logon' if IS_WIN else 'systemd user'})")
    if active:
        port = env_port()
        print(f"saúde:     {ok('/health ok') if health_ok(port) else warn('não responde')}")
        print(f"url:       http://localhost:{port}")
    _print_llm_status()
    return 0


def cmd_service(args) -> int:
    action = args.action
    if action is None:
        if not _interactive():
            print(err("uso: helena service {install|uninstall|status|start|stop|restart}"))
            return 2
        action = select_menu(
            "O que fazer com o serviço?",
            [
                ("status", "ver estado"),
                ("install", "instalar (sobe no login)"),
                ("start", "iniciar"),
                ("stop", "parar"),
                ("restart", "reiniciar"),
                ("uninstall", "remover"),
            ],
        )
        if action is None:
            print(warn("cancelado."))
            return 1
    if action == "install":
        return _service_install()
    if action == "uninstall":
        return _service_uninstall()
    if action == "status":
        return _service_status()
    if action in ("start", "stop", "restart"):
        if not service_installed():
            print(warn("Serviço não instalado.")); return 1
        _service_action(action)
        print(ok(f"✓ serviço: {action}"))
        return 0
    return 2


def cmd_test(args) -> int:
    """Roda o servidor em PRIMEIRO PLANO (logs ao vivo, Ctrl+C para parar) —
    para testar antes de instalar como serviço."""
    port = env_port()
    if service_installed() and _service_active():
        print(warn("O serviço está rodando — pare com 'helena service stop' antes de testar."))
        return 1
    if running_pid() or health_ok(port):
        print(warn(f"Já há um servidor ativo na porta {port} — pare com 'helena stop' antes."))
        return 1
    if not ENV.exists():
        print(warn("Sem .env — rode 'helena setup' primeiro."))
        return 1
    print(bold(f"Modo teste na porta {port} — Ctrl+C para parar.\n"))
    os.chdir(str(ROOT))
    py = _server_python()
    os.execv(py, [py, str(RUN_PY)])  # substitui o processo → logs ao vivo + Ctrl+C
    return 0  # inalcançável


def cmd_autoupdate(args) -> int:
    action = args.action
    if action is None:
        if not _interactive():
            print(err("uso: helena autoupdate {on|off}"))
            return 2
        action = select_menu(
            "Auto-update diário (git pull + uv sync)?",
            [("on", "ligar"), ("off", "desligar")],
        )
        if action is None:
            print(warn("cancelado."))
            return 1
    on = action == "on"
    if IS_WIN:
        if on:
            tr = f'cmd /c cd /d "{ROOT}" && "{ROOT / "helena.cmd"}" update git'
            subprocess.run(["schtasks", "/Create", "/TN", _WIN_UPD_TASK, "/SC", "DAILY",
                            "/ST", "04:00", "/F", "/TR", tr], capture_output=True)
            print(ok("✓ auto-update diário (04:00) ativado."))
        else:
            subprocess.run(["schtasks", "/Delete", "/TN", _WIN_UPD_TASK, "/F"], capture_output=True)
            print(ok("✓ auto-update desativado."))
        return 0

    if not on:
        _systemctl("disable", "--now", f"{SERVICE_NAME}-update.timer")
        _UPD_TIMER.unlink(missing_ok=True)
        _UPD_SERVICE.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        print(ok("✓ auto-update desativado."))
        return 0

    _UNIT_DIR.mkdir(parents=True, exist_ok=True)
    _UPD_SERVICE.write_text(
        "[Unit]\nDescription=Helena — auto-update (git)\n\n"
        "[Service]\nType=oneshot\n"
        f"WorkingDirectory={ROOT}\n"
        f'Environment="PATH={os.environ.get("PATH", "")}"\n'
        f"ExecStart={ROOT / 'helena'} update git\n"
    )
    _UPD_TIMER.write_text(
        "[Unit]\nDescription=Helena — auto-update diário\n\n"
        "[Timer]\nOnCalendar=daily\nPersistent=true\n\n"
        "[Install]\nWantedBy=timers.target\n"
    )
    _systemctl("daemon-reload")
    r = _systemctl("enable", "--now", f"{SERVICE_NAME}-update.timer")
    if r.returncode != 0:
        print(err("falha ao ativar o timer:")); print(dim(r.stderr)); return 1
    print(ok("✓ auto-update diário ativado (git pull + uv sync + restart se mudou)."))
    print(dim("Resultados vão para o journal: journalctl --user -u helena-update"))
    return 0


def cmd_audit(args) -> int:
    """Mostra o que a Helena executou na máquina (trilha de auditoria)."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado."))
        return 1
    con = sqlite3.connect(str(dbp))
    try:
        rows = con.execute(
            "SELECT created_at, kind, exit_code, detail FROM audit_entries "
            "ORDER BY id DESC LIMIT ?", (args.lines,),
        ).fetchall()
    except sqlite3.OperationalError:
        print(dim("Sem auditoria ainda (reinicie o servidor para migrar)."))
        return 0
    if not rows:
        print(dim("Nenhuma ação registrada ainda."))
        return 0
    _section(f"Últimas {len(rows)} ações")
    for ts, kind, code, detail in rows:
        rc = "" if code is None else dim(f" rc={code}")
        detail = (detail or "").replace("\n", " ⏎ ")
        print(f"  {dim(ts[:19])}  {bold(kind):<8}{rc}  {detail[:90]}")
    return 0


def cmd_backup(args) -> int:
    """Snapshot consistente do banco (SQLite backup API — seguro mesmo com WAL)."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado."))
        return 1
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"helena-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(dbp))
    dst = sqlite3.connect(str(dest))
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    print(ok(f"✓ backup: {dest}  ({dest.stat().st_size // 1024} KB)"))
    # rotação: mantém só os N mais recentes
    old = sorted(backup_dir.glob("helena-*.db"))[:-args.keep]
    for f in old:
        f.unlink(missing_ok=True)
    if old:
        print(dim(f"   ({len(old)} backup(s) antigo(s) removido(s); mantendo {args.keep})"))
    return 0


def cmd_panic(args) -> int:
    """Kill switch: revoga TODAS as permissões e nega comandos pendentes."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado."))
        return 1
    con = sqlite3.connect(str(dbp))
    con.execute("PRAGMA busy_timeout=5000")
    with con:
        n_users = con.execute(
            "UPDATE users SET is_principal=0, shell_full_control=0, sudo_enabled=0 "
            "WHERE is_principal=1 OR shell_full_control=1 OR sudo_enabled=1"
        ).rowcount
        try:
            n_cmds = con.execute(
                "UPDATE shell_commands SET status='denied' WHERE status='pending'"
            ).rowcount
        except sqlite3.OperationalError:
            n_cmds = 0
    print(err("🛑 PÂNICO — permissões revogadas de todos os usuários."))
    print(dim(f"   {n_users} usuário(s) rebaixado(s); {n_cmds} comando(s) pendente(s) negado(s)."))
    print(dim("   A Helena não mexe mais na máquina. Para matar o servidor: 'helena stop'."))
    print(dim("   Reative depois com: helena users principal|fullcontrol <username>"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="helena", description="Gerencia o servidor da Helena.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("setup", help="🛠️  configurar variáveis de ambiente (interativo)")
    sp.add_argument("--advanced", action="store_true", help="também pergunta modelos/voz")
    sp.set_defaults(func=cmd_setup)

    cf = sub.add_parser("config", help="⚙️  ler/gravar variáveis sem interação")
    cf.add_argument("action", nargs="?", choices=["list", "get", "set"], default=None)
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.set_defaults(func=cmd_config)

    sub.add_parser("start", help="▶️  iniciar o servidor em background").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="⏹️  parar o servidor").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="🔁 reiniciar o servidor").set_defaults(func=cmd_restart)
    sub.add_parser("status", help="📊 ver se está rodando").set_defaults(func=cmd_status)

    lg = sub.add_parser("logs", help="📜 ver o log do servidor")
    lg.add_argument("-f", "--follow", action="store_true", help="acompanhar em tempo real")
    lg.add_argument("-n", "--lines", type=int, default=60, help="quantas linhas mostrar")
    lg.set_defaults(func=cmd_logs)

    up = sub.add_parser("update", help="⬆️  atualizar: 'git' (puxa do remoto) ou 'code' (aplica mudanças locais)")
    up.add_argument("action", nargs="?", choices=["git", "code"], default=None)
    up.set_defaults(func=cmd_update)

    sub.add_parser("test", help="🧪 rodar em primeiro plano p/ testar antes de instalar (Ctrl+C para)").set_defaults(func=cmd_test)

    sv = sub.add_parser("service", help="🧰 instalar/gerir como serviço do sistema (sobe no login)")
    sv.add_argument("action", nargs="?", choices=["install", "uninstall", "status", "start", "stop", "restart"], default=None)
    sv.set_defaults(func=cmd_service)

    au = sub.add_parser("autoupdate", help="🔄 ligar/desligar auto-update diário (git)")
    au.add_argument("action", nargs="?", choices=["on", "off"], default=None)
    au.set_defaults(func=cmd_autoupdate)

    ad = sub.add_parser("audit", help="📋 ver o que a Helena executou (shell/desktop)")
    ad.add_argument("-n", "--lines", type=int, default=40, help="quantas ações mostrar")
    ad.set_defaults(func=cmd_audit)

    bk = sub.add_parser("backup", help="💾 snapshot do banco (data/backups/)")
    bk.add_argument("--keep", type=int, default=10, help="quantos backups manter")
    bk.set_defaults(func=cmd_backup)

    sub.add_parser("panic", help="🛑 revogar TODAS as permissões (kill switch)").set_defaults(func=cmd_panic)

    sub.add_parser("doctor", help="🩺 checar pré-requisitos e estado").set_defaults(func=cmd_doctor)

    md = sub.add_parser("models", help="🧠 gerenciar modelos locais (Ollama): listar/trocar/baixar/remover")
    md.add_argument("action", nargs="?", choices=["list", "use", "pull", "remove"], default=None)
    md.add_argument("name", nargs="?", help="nome/tag do modelo (ex.: qwen2.5:7b)")
    md.set_defaults(func=cmd_models)

    pv = sub.add_parser("provider", help="🔀 trocar o cérebro da Helena entre 'gemini' (nuvem) e 'ollama' (local)")
    pv.add_argument("name", nargs="?", choices=["gemini", "ollama"], default=None)
    pv.set_defaults(func=cmd_provider)

    us = sub.add_parser("users", help="👥 listar usuários, definir permissão, ou atribuir email a conta antiga")
    us.add_argument("action", nargs="?", choices=["list", "principal", "fullcontrol", "normal", "sudo", "nosudo", "email"], default=None)
    us.add_argument("identifier", nargs="?", help="email (ou username antigo)")
    us.add_argument("value", nargs="?", help="novo email — só usado com a ação 'email'")
    us.set_defaults(func=cmd_users)

    ch = sub.add_parser("chat", help="💬 chat em texto no terminal (login por email+senha)")
    ch.add_argument("--server", help="URL da Helena (default: http://127.0.0.1:<HELENA_PORT>)")
    ch.add_argument("--logout", action="store_true", help="apaga a sessão local salva e sai")
    ch.set_defaults(func=cmd_chat)

    gl = sub.add_parser(
        "goal",
        help="🎯 dá um propósito à Helena: ela pesquisa, planeja e implementa automações",
    )
    gl.add_argument("purpose", nargs="?", help="descrição do propósito (ou deixa vazio pra digitar)")
    gl.add_argument("--server", help="URL da Helena (default: http://127.0.0.1:<HELENA_PORT>)")
    gl.set_defaults(func=cmd_goal)
    return p


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    parser = build_parser()

    if not raw:
        # sem comando nenhum: entra direto no chat (igual a `helena chat`) —
        # `-h`/`--help` continua sendo o jeito de ver a lista completa de comandos.
        args = parser.parse_args(["chat"])
        return args.func(args)
    if raw[0] in ("-h", "--help"):
        print(_banner())
        print()
        # cai no parse_args abaixo, que imprime a ajuda de verdade e sai

    args = parser.parse_args(raw)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n" + warn("interrompido."))
        return 130


if __name__ == "__main__":
    sys.exit(main())
