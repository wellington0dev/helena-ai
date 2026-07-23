"""Cliente de terminal pro `helena goal`: dá um propósito à Helena e ela
pesquisa, planeja e (depois de aprovado) implementa — reusa o mesmo backend
síncrono do chat (`chat_cli.py`), só que semeado com uma instrução de
"pesquise e planeje primeiro, espere aprovação" e capaz de detectar/resolver
cards de aprovação de shell/SSH ali no terminal, sem precisar do chat/app.

Não é stdlib-only (usa `requests`, via `chat_cli`) — import tardio em
`cli.py`, mesmo padrão do `helena chat`.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

import cli_prompt
from chat_cli import (
    SEND_TIMEOUT,
    ChatCliError,
    _authenticate,
    _print_reply,
    _raise_from_response,
    _reauth_or_none,
    api_history,
    api_send,
    bold,
    clear_session,
    dim,
    err,
    load_session,
    ok,
    save_session,
    warn,
)
from cli_select import select_menu

_TTY = sys.stdout.isatty()

HISTORY_FILENAME = "cli_goal_history"
SLASH_COMMANDS = ["/historico", "/aguardar", "/aprovar", "/logout", "/sair"]

_SEED_TEMPLATE = (
    "Quero configurar este dispositivo/minhas automações para um propósito. "
    "Primeiro PESQUISE o que for necessário (ferramentas, integrações, "
    "credenciais) e monte um PLANO numerado e acionável — use "
    "run_background_job (research e/ou plan) se o tema pedir isso. NÃO "
    "execute nada ainda (nada de shell, ssh, nem criar automação) — só "
    "pesquise e planeje. Quando o plano estiver pronto, me avise claramente "
    "e PARE, esperando minha aprovação explícita antes de qualquer execução. "
    "Se precisar de alguma credencial/chave que só eu tenho, inclua isso "
    "como um passo do plano (vou te dar quando aprovar).\n\n"
    f"Propósito: {{purpose}}"
)

_APPROVE_MESSAGE = "Plano aprovado! Pode começar a implementar, passo a passo."


# --------------------------------------------------------------------------- #
# chamada nova: decisão de aprovação (reusa /commands/<id>/decision)
# --------------------------------------------------------------------------- #

def api_decide(base_url: str, token: str, cmd_id: int, decision: str) -> dict:
    try:
        r = requests.post(
            f"{base_url}/commands/{cmd_id}/decision",
            headers={"Authorization": f"Bearer {token}"},
            json={"decision": decision},
            timeout=SEND_TIMEOUT,  # pode reinvocar o agente — mesma duração de um turno
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


# --------------------------------------------------------------------------- #
# detecção e resolução de cards de aprovação pendentes
# --------------------------------------------------------------------------- #

def _pending_approvals(replies: list[dict]) -> list[dict]:
    return [
        m for m in replies
        if m.get("tool_name") == "shell_request"
        and (m.get("media_meta") or {}).get("status") == "pending"
    ]


def _handle_approval(base_url: str, token: str, msg: dict) -> list[dict]:
    meta = msg.get("media_meta") or {}
    cmd_id = meta.get("cmd_id")
    command = meta.get("command") or ""
    host = meta.get("target_host")
    motivo = meta.get("motivo")

    print()
    where = f"via SSH em {bold(host)}" if host else "nesta máquina"
    print(warn(f"🔐 Helena quer rodar um comando {where}:"))
    print(f"   $ {command}")
    if motivo:
        print(dim(f"   motivo: {motivo}"))

    always_label = f"permitir sempre (esse comando em {host})" if host else "permitir sempre (esse comando)"
    decision = select_menu(
        "O que fazer?",
        [("allow", "permitir uma vez"), ("always", always_label), ("deny", "negar")],
        default=0,
    )
    if decision is None:
        print(warn("cancelado — negando por segurança."))
        decision = "deny"

    if cmd_id is None:
        print(err("✗ card de aprovação sem cmd_id — não dá pra decidir."))
        return []

    try:
        resp = api_decide(base_url, token, cmd_id, decision)
    except ChatCliError as e:
        if str(e) == "__EXPIRED__":
            print(warn("sessão expirada no meio da aprovação — faça login de novo e tente outra vez."))
        else:
            print(err(f"✗ {e}"))
        return []
    return resp.get("messages", [])


def _process_replies(base_url: str, token: str, replies: list[dict], since_id: int) -> int:
    """Imprime as respostas e resolve, em cadeia, qualquer aprovação
    pendente — uma implementação de várias etapas encadeia sozinha."""
    for m in replies:
        since_id = max(since_id, m.get("id") or 0)
        _print_reply(m)
    for m in replies:
        meta = m.get("media_meta") or {}
        if m.get("tool_name") == "shell_request" and meta.get("status") == "pending":
            more = _handle_approval(base_url, token, m)
            since_id = _process_replies(base_url, token, more, since_id)
    return since_id


# --------------------------------------------------------------------------- #
# espera por resultado assíncrono (research/plan em segundo plano)
# --------------------------------------------------------------------------- #

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _wait_for_job_result(base_url: str, token: str, since_id: int, timeout_s: float = 600.0) -> list[dict]:
    """Poll em GET /messages (api_history) até aparecer algo com id >
    since_id — assim o CLI sabe que um job em segundo plano terminou, sem
    WebSocket e sem endpoint novo de status de job."""
    deadline = time.monotonic() + timeout_s
    frame = 0
    while time.monotonic() < deadline:
        try:
            msgs = api_history(base_url, token, limit=20)
        except ChatCliError as e:
            if _TTY:
                sys.stdout.write("\r" + " " * 40 + "\r")
            if str(e) == "__EXPIRED__":
                print(warn("sessão expirada — rode /sair e entre de novo."))
            else:
                print(err(f"✗ {e}"))
            return []
        new = [m for m in msgs if (m.get("id") or 0) > since_id]
        if new:
            if _TTY:
                sys.stdout.write("\r" + " " * 40 + "\r")
            return new
        if _TTY:
            ch = _SPINNER_FRAMES[frame % len(_SPINNER_FRAMES)]
            sys.stdout.write(f"\r{ch} esperando a Helena terminar...")
            sys.stdout.flush()
            frame += 1
        time.sleep(2.0)
    if _TTY:
        sys.stdout.write("\r" + " " * 40 + "\r")
    print(warn("nada novo ainda — ela pode continuar trabalhando; tente /aguardar de novo em instantes."))
    return []


# --------------------------------------------------------------------------- #
# fluxo interativo
# --------------------------------------------------------------------------- #

def _send_and_process(base_url: str, token: str, content: str, since_id: int) -> tuple[int, str | None]:
    """Manda uma mensagem e processa a resposta. Devolve (since_id, token) —
    token pode mudar se precisou reautenticar."""
    try:
        resp = api_send(base_url, token, content)
    except ChatCliError as e:
        if str(e) == "__EXPIRED__":
            return since_id, None
        if str(e) == "__TIMEOUT__":
            print(warn(
                "⏳ demorou demais pra responder. O servidor pode já ter processado — "
                "use /aguardar ou /historico em vez de reenviar (evita duplicar o turno)."
            ))
            return since_id, token
        print(err(f"✗ {e}"))
        return since_id, token
    since_id = _process_replies(base_url, token, resp.get("replies", []), since_id)
    return since_id, token


def run(args, data_dir: Path, default_base_url: str) -> int:
    """Entry point chamado por cli.py::cmd_goal."""
    base_url = (args.server or default_base_url).rstrip("/")

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

    purpose = (getattr(args, "purpose", None) or "").strip()
    if not purpose:
        print(bold("\nQual é o propósito? (o que você quer que a Helena configure/automatize)"))
        result = cli_prompt.ask("propósito", data_dir, HISTORY_FILENAME)
        if result is None:
            print()
            return 1
        purpose = result[0].strip()
    if not purpose:
        print(err("propósito vazio."))
        return 1

    print(dim("\nEnviando o propósito pra Helena pesquisar e planejar...\n"))
    since_id = 0
    since_id, token = _send_and_process(base_url, token, _SEED_TEMPLATE.format(purpose=purpose), since_id)
    if token is None:
        token = _reauth_or_none(data_dir, base_url)
        if token is None:
            return 1
        since_id, token = _send_and_process(base_url, token, _SEED_TEMPLATE.format(purpose=purpose), since_id)

    print(dim(
        "\nComandos: /aguardar (espera pesquisa/plano em segundo plano), "
        "/aprovar (autoriza a implementação do plano), /historico, /sair\n"
    ))

    while True:
        toolbar = f"{who} @ {base_url}"
        result = cli_prompt.ask("você", data_dir, HISTORY_FILENAME, SLASH_COMMANDS, toolbar)
        if result is None:
            print()
            break
        line = result[0].strip()
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
        if line == "/aguardar":
            new = _wait_for_job_result(base_url, token, since_id)
            since_id = _process_replies(base_url, token, new, since_id)
            continue
        if line == "/aprovar":
            line = _APPROVE_MESSAGE

        since_id, new_token = _send_and_process(base_url, token, line, since_id)
        if new_token is None:
            new_token = _reauth_or_none(data_dir, base_url)
            if new_token is None:
                break
            since_id, new_token = _send_and_process(base_url, new_token, line, since_id)
        token = new_token

    return 0
