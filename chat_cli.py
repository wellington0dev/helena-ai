"""Cliente de chat em texto puro pro CLI (`helena chat`). Login por
email+senha, mesmo backend REST síncrono do app (POST/GET /messages). Não é
stdlib-only (usa requests) — por isso importado tardiamente por cli.py."""
from __future__ import annotations

import getpass
import json
import os
import shutil
import sys
from pathlib import Path

import requests

import cli_prompt
from cli_select import confirm

_TTY = sys.stdout.isatty()

SESSION_FILENAME = "cli_session.json"
HISTORY_FILENAME = "cli_chat_history"
SLASH_COMMANDS = ["/historico", "/imagem", "/colado", "/logout", "/sair"]
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


# ---------- tela de entrada (arte ASCII "HELENA" + dicas de uso) ----------

_GLYPHS = {
    "H": ["█   █", "█   █", "█████", "█   █", "█   █"],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "L": ["█    ", "█    ", "█    ", "█    ", "█████"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
}


def _ascii_word(word: str) -> list[str]:
    rows = ["", "", "", "", ""]
    for i, ch in enumerate(word):
        glyph = _GLYPHS[ch]
        sep = " " if i else ""
        for r in range(5):
            rows[r] += sep + glyph[r]
    return rows


def _intro_box(who: str, base_url: str) -> str:
    """Banner de entrada do chat: "HELENA" em ASCII à esquerda, dicas de uso
    à direita — cai pra uma linha simples se o terminal não for largo o
    bastante (ou não for TTY), pra nunca quebrar layout numa automação/log."""
    left = _ascii_word("HELENA") + ["", f"Bem-vindo(a) de volta, {who}!", base_url]
    right = [
        "Como usar",
        "",
        "Enter envia · Alt+Enter quebra linha · ↑ histórico",
        "/historico    ver mensagens anteriores",
        "/imagem       colar imagem copiada",
        "/colado <N>   ver texto colado colapsado",
        "/logout       sair e apagar sessão local",
        "/sair         encerrar",
        "",
        "outros comandos do CLI: helena -h",
    ]

    height = max(len(left), len(right))
    left += [""] * (height - len(left))
    right += [""] * (height - len(right))
    left_w = max(len(l) for l in left)
    right_w = max(len(l) for l in right)
    total_w = left_w + right_w + 3  # " │ " entre as colunas

    if not _TTY or shutil.get_terminal_size((80, 24)).columns < total_w + 4:
        return f"{bold('HELENA')} — assistente pessoal"

    def _color_left(i: int, text: str) -> str:
        if i < 5 or i == 6:
            return bold(text)
        if i == 7:
            return dim(text)
        return text

    def _color_right(i: int, text: str) -> str:
        if i == 0:
            return bold(text)
        if i in (2, 9):
            return dim(text)
        return text

    sep = dim("│")
    rows = [
        f"{_color_left(i, left[i].ljust(left_w))} {sep} {_color_right(i, right[i].ljust(right_w))}"
        for i in range(height)
    ]
    border = dim("│")
    top = dim("╭" + "─" * (total_w + 2) + "╮")
    bottom = dim("╰" + "─" * (total_w + 2) + "╯")
    middle = [f"{border} {r} {border}" for r in rows]
    return "\n".join([top, *middle, bottom])


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


def api_upload_media(base_url, token, data: bytes, filename: str) -> dict:
    """Sobe um arquivo (ex.: imagem colada da área de transferência) e devolve
    media_url/media_type/media_meta pra anexar no próximo api_send."""
    try:
        r = requests.post(
            f"{base_url}/media/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, data)},
            timeout=LOGIN_TIMEOUT,
        )
    except requests.RequestException as e:
        raise ChatCliError(f"erro de rede no upload: {e}")
    if r.status_code == 401:
        raise ChatCliError("__EXPIRED__")
    if r.status_code >= 400:
        _raise_from_response(r, f"HTTP {r.status_code}")
    return r.json()


def api_send(base_url, token, content, media_url=None, media_type=None) -> dict:
    # manda o diretório atual do terminal: é de onde a Helena está sendo chamada,
    # então ela edita/cria código e roda comandos já a partir daqui.
    try:
        cwd = os.getcwd()
    except OSError:
        cwd = None
    payload = {"content": content, "cwd": cwd}
    if media_url:
        payload["media_url"] = media_url
        payload["media_type"] = media_type
    try:
        r = requests.post(
            f"{base_url}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
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


def _print_pasted(line: str, pastes: dict[str, str]) -> None:
    """`/colado` (lista os colados colapsados da última mensagem) ou
    `/colado N` (mostra o texto completo por trás do placeholder #N)."""
    if not pastes:
        print(dim("nenhum texto colado colapsado na sua última mensagem."))
        return
    parts = line.split(maxsplit=1)
    if len(parts) == 1:
        for placeholder in pastes:
            print(dim(placeholder))
        return
    arg = parts[1].strip().lstrip("#")
    for placeholder, original in pastes.items():
        if f"#{arg}" in placeholder:
            print(original)
            return
    print(err(f"colado #{arg} não encontrado na sua última mensagem."))


def _capture_clipboard_image(base_url, token) -> dict | None:
    """`/imagem` — lê a área de transferência e sobe pro servidor. Devolve
    o media_url/media_type pra anexar na PRÓXIMA mensagem enviada, ou None
    se não tinha imagem/a ferramenta de SO faltou (já avisado ao usuário)."""
    import clipboard_image

    try:
        data = clipboard_image.read_clipboard_image()
    except clipboard_image.ClipboardImageError as e:
        print(err(f"✗ {e}"))
        return None
    if data is None:
        print(warn("nenhuma imagem na área de transferência."))
        return None
    media = api_upload_media(base_url, token, data, "colado.png")
    print(ok(f"✓ imagem anexada ({len(data)} bytes) — vai junto da sua próxima mensagem."))
    return media


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

    print()
    print(_intro_box(who, base_url))
    print()

    pending_media: dict | None = None
    last_pastes: dict[str, str] = {}

    while True:
        toolbar = f"{who} @ {base_url}" + ("  📎 imagem anexada" if pending_media else "")
        result = cli_prompt.ask("você", data_dir, HISTORY_FILENAME, SLASH_COMMANDS, toolbar)
        if result is None:
            print()
            break
        line, pastes = result
        line = line.strip()
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
        if line.startswith("/colado"):
            _print_pasted(line, last_pastes)
            continue
        if line == "/imagem":
            try:
                pending_media = _capture_clipboard_image(base_url, token)
            except ChatCliError as e:
                if str(e) == "__EXPIRED__":
                    token = _reauth_or_none(data_dir, base_url)
                    if token is None:
                        break
                else:
                    print(err(f"✗ {e}"))
            continue

        last_pastes = pastes
        try:
            resp = api_send(
                base_url, token, line,
                media_url=pending_media.get("media_url") if pending_media else None,
                media_type=pending_media.get("media_type") if pending_media else None,
            )
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
        pending_media = None

        for reply in resp.get("replies", []):
            _print_reply(reply)

    return 0
