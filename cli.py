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
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

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
]
SECRET_KEYS = {"GEMINI_API_KEY", "JWT_SECRET_KEY"}
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


# ---------- gerência do .env ----------
def read_env_values() -> dict[str, str]:
    """Lê os pares KEY=VALUE ativos (não comentados) do .env."""
    vals: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
            if m:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def set_env_values(updates: dict[str, str]) -> None:
    """Atualiza (ou insere) chaves no .env preservando comentários e ordem.
    Substitui inclusive linhas comentadas do tipo `# KEY=` (placeholders)."""
    if not ENV.exists() and ENV_EXAMPLE.exists():
        ENV.write_text(ENV_EXAMPLE.read_text())
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    ENV.write_text("\n".join(out) + "\n")


def mask(value: str) -> str:
    if not value:
        return dim("(vazio)")
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"


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
def cmd_setup(args) -> int:
    print(bold("Configuração da Helena"))
    print(dim("Enter mantém o valor atual/default entre colchetes.\n"))
    current = read_env_values()
    updates: dict[str, str] = {}

    for key, default, desc, secret in CORE_FIELDS:
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
    if not final.get("GEMINI_API_KEY"):
        print(warn("⚠ GEMINI_API_KEY ainda está vazia — a IA não vai funcionar sem ela."))
    print(dim("Rode 'helena start' para iniciar."))
    return 0


def cmd_config(args) -> int:
    if args.action == "list":
        vals = read_env_values()
        if not vals:
            print(dim("Nenhuma variável configurada (rode 'helena setup')."))
            return 0
        for key, val in vals.items():
            shown = mask(val) if key in SECRET_KEYS else val
            print(f"  {bold(key)}={shown}")
        return 0
    if args.action == "get":
        if not args.key:
            print(err("uso: helena config get CHAVE"))
            return 2
        val = read_env_values().get(args.key)
        if val is None:
            print(dim(f"{args.key} não configurada"))
            return 1
        print(val)
        return 0
    if args.action == "set":
        if not args.key or args.value is None:
            print(err("uso: helena config set CHAVE VALOR"))
            return 2
        set_env_values({args.key: args.value})
        print(ok(f"✓ {args.key} atualizado"))
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
    print(dim(f"iniciando (pid {proc.pid}) na porta {port}..."))
    for _ in range(40):  # ~20s
        if proc.poll() is not None:
            print(err("✗ o servidor saiu logo ao subir. Últimas linhas do log:"))
            _print_log_tail(20)
            PID_FILE.unlink(missing_ok=True)
            return 1
        if health_ok(port):
            print(ok(f"✓ rodando em http://localhost:{port}  (pid {proc.pid})"))
            return 0
        time.sleep(0.5)
    print(warn("Subiu mas /health ainda não respondeu. Veja 'helena logs'."))
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
    mode = getattr(args, "action", None) or "git"
    return _update_code() if mode == "code" else _update_git()


def cmd_doctor(args) -> int:
    def line(good, label, extra=""):
        mark = ok("✓") if good else err("✗")
        print(f"  {mark} {label}{('  ' + dim(extra)) if extra else ''}")

    import shutil
    line(bool(shutil.which("uv")), "uv instalado", "" if shutil.which("uv") else "instale: https://astral.sh/uv")
    venv_py = Path(_server_python())
    line(venv_py.exists() and ".venv" in str(venv_py), "ambiente (.venv) criado", "" if venv_py.exists() else "rode 'uv sync'")
    line(ENV.exists(), ".env presente", "" if ENV.exists() else "rode 'helena setup'")
    vals = read_env_values()
    line(bool(vals.get("GEMINI_API_KEY")), "GEMINI_API_KEY configurada")
    jwt = vals.get("JWT_SECRET_KEY", "")
    line(bool(jwt) and jwt != JWT_PLACEHOLDER, "JWT_SECRET_KEY configurada")
    pid = running_pid()
    port = env_port()
    line(bool(pid), "servidor rodando", f"pid {pid}" if pid else "helena start")
    if pid:
        line(health_ok(port), f"/health responde na porta {port}")
    return 0


def _find_user_row(con: sqlite3.Connection, identifier: str):
    """Busca por email primeiro (identificador principal agora); cai pra
    username (contas antigas / bookkeeping interno gerado no registro)."""
    row = con.execute("SELECT id, username FROM users WHERE email=?", (identifier,)).fetchone()
    if row is None:
        row = con.execute("SELECT id, username FROM users WHERE username=?", (identifier,)).fetchone()
    return row


def cmd_users(args) -> int:
    """Lista usuários e define permissão/email."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado — rode o servidor ao menos uma vez ('helena start')."))
        return 1
    con = sqlite3.connect(str(dbp))
    con.execute("PRAGMA busy_timeout=5000")
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if not {"is_principal", "shell_full_control", "name"} <= cols:
        print(warn("Colunas novas ainda não existem — reinicie o servidor ('helena restart') para migrar."))
        return 1

    action = args.action or "list"

    if action == "list":
        rows = con.execute(
            "SELECT id, username, email, name, is_principal, shell_full_control FROM users ORDER BY id"
        ).fetchall()
        if not rows:
            print(dim("Nenhum usuário cadastrado."))
            return 0
        print(bold("Usuários:"))
        for uid, uname, email, name, principal, full in rows:
            label = email or f"{uname} (sem email — conta antiga)"
            if name:
                label = f"{label}  [{name}]"
            if full:
                tag = err("⚡ controle absoluto")
            elif principal:
                tag = ok("★ principal")
            else:
                tag = dim("normal")
            print(f"  {uid:>3}  {label:<40} {tag}")
        print(dim("\nNíveis: normal → principal (com aprovação) → fullcontrol (sem aprovação)."))
        print(dim("Ex.: helena users principal <email> | helena users fullcontrol <email> | helena users normal <email>"))
        print(dim("Conta antiga sem email? helena users email <username_antigo> <novo@email.com>"))
        return 0

    if action == "email":
        if not args.identifier or not args.value:
            print(err("uso: helena users email <email_ou_username_atual> <novo_email>"))
            return 2
        row = _find_user_row(con, args.identifier)
        if row is None:
            print(err(f"usuário '{args.identifier}' não encontrado (veja 'helena users')"))
            return 1
        uid, uname = row
        new_email = args.value.strip().lower()
        try:
            with con:
                con.execute("UPDATE users SET email=? WHERE id=?", (new_email, uid))
        except sqlite3.IntegrityError:
            print(err(f"email '{new_email}' já está em uso por outra conta."))
            return 1
        print(ok(f"✓ {uname} (id {uid}) agora tem email {new_email}."))
        return 0

    if not args.identifier:
        print(err(f"uso: helena users {action} <email_ou_username>"))
        return 2
    row = _find_user_row(con, args.identifier)
    if row is None:
        print(err(f"usuário '{args.identifier}' não encontrado (veja 'helena users')"))
        return 1
    uid, uname = row

    principal_v, full_v = {"fullcontrol": (1, 1), "principal": (1, 0), "normal": (0, 0)}[action]
    with con:
        con.execute(
            "UPDATE users SET is_principal=?, shell_full_control=? WHERE id=?",
            (principal_v, full_v, uid),
        )
    if action == "fullcontrol":
        print(err(f"⚡ {uname} agora tem CONTROLE ABSOLUTO — a Helena roda QUALQUER comando SEM pedir aprovação."))
        print(dim("   Cada comando ainda aparece no chat depois de rodar. Reverta com: helena users principal|normal " + args.identifier))
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
    for _ in range(30):
        if health_ok(port):
            print(ok(f"✓ no ar em http://localhost:{port}"))
            return 0
        time.sleep(0.5)
    print(warn("iniciado, mas /health ainda não respondeu. Veja 'helena logs'."))
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
    return 0


def cmd_service(args) -> int:
    action = args.action
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
    on = args.action == "on"
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
    print(bold(f"Últimas {len(rows)} ações executadas:"))
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
            "UPDATE users SET is_principal=0, shell_full_control=0 "
            "WHERE is_principal=1 OR shell_full_control=1"
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

    sp = sub.add_parser("setup", help="configurar variáveis de ambiente (interativo)")
    sp.add_argument("--advanced", action="store_true", help="também pergunta modelos/voz")
    sp.set_defaults(func=cmd_setup)

    cf = sub.add_parser("config", help="ler/gravar variáveis sem interação")
    cf.add_argument("action", choices=["list", "get", "set"])
    cf.add_argument("key", nargs="?")
    cf.add_argument("value", nargs="?")
    cf.set_defaults(func=cmd_config)

    sub.add_parser("start", help="iniciar o servidor em background").set_defaults(func=cmd_start)
    sub.add_parser("stop", help="parar o servidor").set_defaults(func=cmd_stop)
    sub.add_parser("restart", help="reiniciar o servidor").set_defaults(func=cmd_restart)
    sub.add_parser("status", help="ver se está rodando").set_defaults(func=cmd_status)

    lg = sub.add_parser("logs", help="ver o log do servidor")
    lg.add_argument("-f", "--follow", action="store_true", help="acompanhar em tempo real")
    lg.add_argument("-n", "--lines", type=int, default=60, help="quantas linhas mostrar")
    lg.set_defaults(func=cmd_logs)

    up = sub.add_parser("update", help="atualizar: 'git' (puxa do remoto) ou 'code' (aplica mudanças locais)")
    up.add_argument("action", nargs="?", choices=["git", "code"], default="git")
    up.set_defaults(func=cmd_update)

    sub.add_parser("test", help="rodar em primeiro plano p/ testar antes de instalar (Ctrl+C para)").set_defaults(func=cmd_test)

    sv = sub.add_parser("service", help="instalar/gerir como serviço do sistema (sobe no login)")
    sv.add_argument("action", choices=["install", "uninstall", "status", "start", "stop", "restart"])
    sv.set_defaults(func=cmd_service)

    au = sub.add_parser("autoupdate", help="ligar/desligar auto-update diário (git)")
    au.add_argument("action", choices=["on", "off"])
    au.set_defaults(func=cmd_autoupdate)

    ad = sub.add_parser("audit", help="ver o que a Helena executou (shell/desktop)")
    ad.add_argument("-n", "--lines", type=int, default=40, help="quantas ações mostrar")
    ad.set_defaults(func=cmd_audit)

    bk = sub.add_parser("backup", help="snapshot do banco (data/backups/)")
    bk.add_argument("--keep", type=int, default=10, help="quantos backups manter")
    bk.set_defaults(func=cmd_backup)

    sub.add_parser("panic", help="🛑 revogar TODAS as permissões (kill switch)").set_defaults(func=cmd_panic)

    sub.add_parser("doctor", help="checar pré-requisitos e estado").set_defaults(func=cmd_doctor)

    us = sub.add_parser("users", help="listar usuários, definir permissão, ou atribuir email a conta antiga")
    us.add_argument("action", nargs="?", choices=["list", "principal", "fullcontrol", "normal", "email"], default="list")
    us.add_argument("identifier", nargs="?", help="email (ou username antigo)")
    us.add_argument("value", nargs="?", help="novo email — só usado com a ação 'email'")
    us.set_defaults(func=cmd_users)

    ch = sub.add_parser("chat", help="chat em texto no terminal (login por email+senha)")
    ch.add_argument("--server", help="URL da Helena (default: http://127.0.0.1:<HELENA_PORT>)")
    ch.add_argument("--logout", action="store_true", help="apaga a sessão local salva e sai")
    ch.set_defaults(func=cmd_chat)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n" + warn("interrompido."))
        return 130


if __name__ == "__main__":
    sys.exit(main())
