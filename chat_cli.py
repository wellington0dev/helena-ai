"""Cliente de chat em texto puro pro CLI (`helena chat`). Login por
email+senha, mesmo backend REST síncrono do app (POST/GET /messages). Não é
stdlib-only (usa requests) — por isso importado tardiamente por cli.py."""
from __future__ import annotations

import getpass
import json
import os
from pathlib import Path

import requests

from cli_select import confirm

SESSION_FILENAME = "cli_session.json"
LOGIN_TIMEOUT = 10
# turno da IA roda tool-calling inline, pode ter várias iterações — generoso
# de propósito. Mesmo assim, um timeout aqui NÃO significa que o turno falhou
# no servidor (que já persiste a resposta assim que termina); ver mensagem
# no loop, que sugere /historico em vez de reenviar automaticamente.
SEND_TIMEOUT = 180


class ChatCliError(Exception):
    pass


def _c(txt, code):
    return f"\033[{code}m{txt}\033[0m"


def ok(t): return _c(t, "32")
def warn(t): return _c(t, "33")
def err(t): return _c(t, "31")
def dim(t): return _c(t, "2")
def bold(t): return _c(t, "1")


# ---------- sessão local ----------

def _session_path(data_dir: Path) -> Path:
    return data_dir / SESSION_FILENAME


def load_session(data_dir: Path) -> dict | None:
    path = _session_path(data_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if data.get("token") else None


def save_session(data_dir: Path, token: str, user: dict, base_url: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(data_dir)
    path.write_text(json.dumps({
        "token": token, "base_url": base_url,
        "user_id": user.get("id"), "name": user.get("name"), "email": user.get("email"),
    }, indent=2))
    try:
        os.chmod(path, 0o600)  # só o dono lê — o arquivo carrega um JWT
    except OSError:
        pass


def clear_session(data_dir: Path) -> None:
    _session_path(data_dir).unlink(missing_ok=True)


# ---------- chamadas HTTP ----------

def _raise_from_response(r, fallback):
    try:
        raise ChatCliError(r.json().get("error", fallback))
    except ValueError:
        raise ChatCliError(fallback)


def api_login(base_url, email, password) -> dict:
    try:
        r = requests.post(f"{base_url}/auth/login", json={"email": email, "password": password}, timeout=LOGIN_TIMEOUT)
    except requests.RequestException as e:
        raise ChatCliError(f"não consegui falar com {base_url} ({e})")
    if r.status_code != 200:
        _raise_from_response(r, "credenciais inválidas")
    return r.json()


def api_register(base_url, name, email, password) -> dict:
    try:
        r = requests.post(
            f"{base_url}/auth/register",
            json={"name": name, "email": email, "password": password},
            timeout=LOGIN_TIMEOUT,
        )
    except requests.RequestException as e:
        raise ChatCliError(f"não consegui falar com {base_url} ({e})")
    if r.status_code != 201:
        _raise_from_response(r, "falha no cadastro")
    return r.json()


def api_send(base_url, token, content) -> dict:
    try:
        r = requests.post(
            f"{base_url}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"content": content},
            timeout=SEND_TIMEOUT,
        )
    except requests.Timeout:
        raise ChatCliError("__TIMEOUT__")
    except requests.RequestException as e:
        raise ChatCliError(f"erro de rede: {e}")
    if r.status_code == 401:
        raise ChatCliError("__EXPIRED__")
    if r.status_code >= 400:
        _raise_from_response(r, f"HTTP {r.status_code}")
    return r.json()


def api_history(base_url, token, limit=20) -> list[dict]:
    r = requests.get(
        f"{base_url}/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": limit},
        timeout=LOGIN_TIMEOUT,
    )
    if r.status_code == 401:
        raise ChatCliError("__EXPIRED__")
    if r.status_code >= 400:
        _raise_from_response(r, f"HTTP {r.status_code}")
    return r.json().get("messages", [])


# ---------- fluxo interativo ----------

def _authenticate(base_url) -> dict:
    print(bold("Helena — login"))
    print(dim(f"servidor: {base_url}"))
    if confirm("Já tem conta?", default=True):
        email = input("Email: ").strip()
        password = getpass.getpass("Senha: ")
        return api_login(base_url, email, password)
    name = input("Nome: ").strip()
    email = input("Email: ").strip()
    password = getpass.getpass("Senha (mín. 6 caracteres): ")
    return api_register(base_url, name, email, password)


def _print_reply(msg):
    if msg.get("tool_name"):
        print(dim(f"  ↳ ferramenta: {msg['tool_name']}"))
    content = (msg.get("content") or "").strip()
    if content:
        print(f"{bold('Helena:')} {content}")


def _reauth_or_none(data_dir, base_url):
    print(warn("sessão expirada — faça login de novo (ou Ctrl+C pra sair)."))
    try:
        data = _authenticate(base_url)
    except ChatCliError as e:
        print(err(f"✗ {e}"))
        return None
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    save_session(data_dir, data["access_token"], data["user"], base_url)
    return data["access_token"]


def run(args, data_dir: Path, default_base_url: str) -> int:
    """Entry point chamado por cli.py::cmd_chat."""
    base_url = (args.server or os.environ.get("HELENA_CHAT_URL") or default_base_url).rstrip("/")

    if args.logout:
        clear_session(data_dir)
        print(ok("✓ sessão local removida."))
        return 0

    session = load_session(data_dir)
    if session and session.get("base_url") == base_url:
        token = session["token"]
        who = session.get("name") or session.get("email") or "você"
        print(ok(f"✓ sessão retomada ({who} @ {base_url})"))
    else:
        try:
            data = _authenticate(base_url)
        except ChatCliError as e:
            print(err(f"✗ {e}"))
            return 1
        except (EOFError, KeyboardInterrupt):
            print()
            return 1
        token = data["access_token"]
        save_session(data_dir, token, data["user"], base_url)
        who = data["user"].get("name") or data["user"].get("email")
        print(ok(f"✓ logado como {who}"))

    print(dim("Digite sua mensagem. Comandos: /historico, /logout, /sair\n"))

    while True:
        try:
            line = input(f"{bold('você>')} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/sair", "/exit", "/quit"):
            break
        if line == "/logout":
            clear_session(data_dir)
            print(ok("✓ sessão local removida."))
            break
        if line == "/historico":
            try:
                for m in api_history(base_url, token):
                    who_m = "você" if m["role"] == "user" else "helena"
                    content = (m.get("content") or "").strip()
                    if content:
                        print(dim(f"[{who_m}] {content}"))
            except ChatCliError as e:
                if str(e) == "__EXPIRED__":
                    token = _reauth_or_none(data_dir, base_url)
                    if token is None:
                        break
                else:
                    print(err(f"✗ {e}"))
            continue

        try:
            resp = api_send(base_url, token, line)
        except ChatCliError as e:
            if str(e) == "__EXPIRED__":
                token = _reauth_or_none(data_dir, base_url)
                if token is None:
                    break
                continue
            if str(e) == "__TIMEOUT__":
                print(warn(
                    "⏳ demorou demais pra responder. O servidor pode já ter processado — "
                    "confira com /historico antes de mandar de novo (evita duplicar o turno)."
                ))
                continue
            print(err(f"✗ {e}"))
            continue

        for reply in resp.get("replies", []):
            _print_reply(reply)

    return 0
