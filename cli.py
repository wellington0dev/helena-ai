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


def cmd_stop(args) -> int:
    pid = running_pid()
    if not pid:
        print(dim("Não está rodando."))
        return 0
    _kill_tree(pid, force=False)
    for _ in range(20):  # espera até 10s o encerramento gracioso
        if running_pid() is None:
            break
        time.sleep(0.5)
    if running_pid() is not None:
        _kill_tree(pid, force=True)
    PID_FILE.unlink(missing_ok=True)
    print(ok("✓ parado."))
    return 0


def cmd_restart(args) -> int:
    cmd_stop(args)
    time.sleep(1)
    return cmd_start(args)


def cmd_status(args) -> int:
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


def cmd_update(args) -> int:
    if not (ROOT / ".git").exists():
        print(warn("Isto não é um clone git (baixado como zip?). Atualize baixando a versão nova."))
        return 1
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print(err("git indisponível ou repositório inválido."))
        return 1
    # árvore suja → não arrisca
    dirty = _git("status", "--porcelain").stdout.strip()
    if dirty:
        print(warn("Há mudanças locais não commitadas — resolva antes de atualizar:"))
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
    print(ok("✓ atualizado! Rode 'helena restart' para aplicar."))
    return 0


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


def cmd_users(args) -> int:
    """Lista usuários e define quem é PRINCIPAL (pode pedir execução de comandos)."""
    dbp = _db_path()
    if not dbp.exists():
        print(warn("Banco não encontrado — rode o servidor ao menos uma vez ('helena start')."))
        return 1
    con = sqlite3.connect(str(dbp))
    con.execute("PRAGMA busy_timeout=5000")
    cols = {r[1] for r in con.execute("PRAGMA table_info(users)")}
    if "is_principal" not in cols or "shell_full_control" not in cols:
        print(warn("Colunas de permissão ainda não existem — reinicie o servidor ('helena restart') para migrar."))
        return 1

    action = args.action or "list"
    if action == "list":
        rows = con.execute(
            "SELECT id, username, is_principal, shell_full_control FROM users ORDER BY id"
        ).fetchall()
        if not rows:
            print(dim("Nenhum usuário cadastrado."))
            return 0
        print(bold("Usuários:"))
        for uid, uname, principal, full in rows:
            if full:
                tag = err("⚡ controle absoluto")
            elif principal:
                tag = ok("★ principal")
            else:
                tag = dim("normal")
            print(f"  {uid:>3}  {uname:<22} {tag}")
        print(dim("\nNíveis: normal → principal (com aprovação) → fullcontrol (sem aprovação)."))
        print(dim("Ex.: helena users principal <username> | helena users fullcontrol <username> | helena users normal <username>"))
        return 0

    if not args.username:
        print(err(f"uso: helena users {action} <username>"))
        return 2
    row = con.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
    if row is None:
        print(err(f"usuário '{args.username}' não encontrado (veja 'helena users')"))
        return 1

    principal_v, full_v = {"fullcontrol": (1, 1), "principal": (1, 0), "normal": (0, 0)}[action]
    with con:
        con.execute(
            "UPDATE users SET is_principal=?, shell_full_control=? WHERE username=?",
            (principal_v, full_v, args.username),
        )
    if action == "fullcontrol":
        print(err(f"⚡ {args.username} agora tem CONTROLE ABSOLUTO — a Helena roda QUALQUER comando SEM pedir aprovação."))
        print(dim("   Cada comando ainda aparece no chat depois de rodar. Reverta com: helena users principal|normal " + args.username))
    elif action == "principal":
        print(ok(f"✓ {args.username} agora é PRINCIPAL — pode pedir comandos (com aprovação no chat)."))
    else:
        print(ok(f"✓ {args.username} agora é usuário normal — não pode executar comandos."))
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

    sub.add_parser("update", help="buscar e aplicar atualizações (git)").set_defaults(func=cmd_update)
    sub.add_parser("doctor", help="checar pré-requisitos e estado").set_defaults(func=cmd_doctor)

    us = sub.add_parser("users", help="listar usuários e definir permissão (normal|principal|fullcontrol)")
    us.add_argument("action", nargs="?", choices=["list", "principal", "fullcontrol", "normal"], default="list")
    us.add_argument("username", nargs="?")
    us.set_defaults(func=cmd_users)
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
