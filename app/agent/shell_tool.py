"""Tool `executar_shell`: a Helena controla o computador — com permissão do usuário.

Fluxo (CLAUDE-style, igual ao de jobs + um portão humano):
- comando já confiado (string EXATA em `shell_approvals`) → executa na hora;
- caso contrário → cria `ShellCommand(pending)` + uma mensagem de pedido de
  permissão no chat (botões Permitir/Negar/Permitir sempre) e devolve um sinal
  de `pending_approval` que faz o loop do agente PARAR (não executa nada).

A execução em si só acontece aqui (server) ou pelo endpoint de decisão — nunca a
partir de dados do cliente além da própria decisão. Rails de segurança: stdin
fechado (não trava em prompts), timeout matando o grupo de processos, saída
limitada, cwd = home do usuário, e log de auditoria de tudo que roda.
"""
import os
import subprocess
from contextvars import ContextVar
from pathlib import Path

from flask import current_app
from google.genai import types

from app.extensions import db, write_lock
from app.models import Message, ShellApproval, ShellCommand, User

IS_WIN = os.name == "nt"

# Orçamento de execuções de shell por turno do agente (defesa em profundidade
# contra encadeamento autônomo). Resetado no início de cada turno.
_shell_count: ContextVar[int] = ContextVar("shell_exec_count", default=0)


def reset_shell_budget() -> None:
    _shell_count.set(0)


EXECUTAR_SHELL_DECL = types.FunctionDeclaration(
    name="executar_shell",
    description=(
        "Executa UM comando no shell/terminal do computador onde você roda "
        "(veja o SO no contexto do dispositivo). Use para controlar a máquina a "
        "pedido do usuário: listar/abrir arquivos, rodar programas, checar o "
        "sistema, etc. Por segurança, o usuário PRECISA autorizar cada comando "
        "novo pelo chat — você NÃO deve assumir que rodou até receber a saída. "
        "Passe UM comando por chamada; encadeie com && se necessário. Adapte a "
        "sintaxe ao sistema operacional do dispositivo."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "comando": types.Schema(
                type=types.Type.STRING,
                description="O comando exato a executar no shell.",
            ),
            "motivo": types.Schema(
                type=types.Type.STRING,
                description="Motivo curto (mostrado ao usuário no pedido de permissão).",
            ),
        },
        required=["comando"],
    ),
)


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True
            )
        else:
            os.killpg(os.getpgid(proc.pid), 9)
    except (ProcessLookupError, OSError):
        pass


def _cap(text: str, limit: int) -> str:
    if text and len(text) > limit:
        return text[:limit] + f"\n[... saída cortada em {limit} caracteres]"
    return text or ""


def run_shell(command: str) -> dict:
    """Roda `command` no shell com os rails de segurança. Devolve
    {exit_code, stdout, stderr, timeout}. Nunca levanta."""
    cfg = current_app.config
    timeout = cfg["SHELL_TIMEOUT_SECONDS"]
    max_out = cfg["SHELL_MAX_OUTPUT"]
    kwargs = dict(
        cwd=str(Path.home()),
        stdin=subprocess.DEVNULL,  # não trava em prompts (sudo/apt)
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        text=True,
        errors="replace",
    )
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True  # grupo próprio p/ matar a árvore

    try:
        proc = subprocess.Popen(command, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": None, "stdout": "", "stderr": f"falha ao iniciar: {exc}", "timeout": False}

    try:
        out, err = proc.communicate(timeout=timeout)
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out, err = "", ""
        err = (err or "") + f"\n[processo morto após {timeout}s de timeout]"
        code, timed_out = None, True

    current_app.logger.info("SHELL exec (rc=%s%s): %s", code, " TIMEOUT" if timed_out else "", command)
    return {
        "exit_code": code,
        "stdout": _cap(out, max_out),
        "stderr": _cap(err, max_out),
        "timeout": timed_out,
    }


def _output_text(command: str, result: dict) -> str:
    """Texto da mensagem de saída mostrada no chat (bloco de terminal)."""
    parts = [f"$ {command}"]
    if result["stdout"]:
        parts.append(result["stdout"].rstrip())
    if result["stderr"].strip():
        parts.append("[stderr]\n" + result["stderr"].rstrip())
    rc = result["exit_code"]
    parts.append(f"[código de saída: {rc if rc is not None else 'timeout'}]")
    return "\n".join(parts)


def _persist_output(user_id: int, command: str, result: dict) -> int:
    """Mensagem de saída do comando (bloco de terminal, visível no chat)."""
    with write_lock:
        msg = Message(
            user_id=user_id,
            role="tool",
            content=_output_text(command, result),
            tool_name="shell_output",
            media_meta={"command": command, "exit_code": result["exit_code"]},
        )
        db.session.add(msg)
        db.session.commit()
        return msg.id


def execute_recorded(rec: ShellCommand) -> int:
    """Executa um ShellCommand já claimado (status running) e persiste a saída.
    Atualiza status para done/error. Devolve o id da mensagem de saída (para o
    agente reagir a ela no re-invoke)."""
    result = run_shell(rec.command)
    out_id = _persist_output(rec.user_id, rec.command, result)
    from app import audit
    audit.record(rec.user_id, "shell", rec.command, result["exit_code"])
    with write_lock:
        rec.status = "error" if (result["timeout"] or (result["exit_code"] not in (0, None))) else "done"
        rec.stdout = result["stdout"]
        rec.stderr = result["stderr"]
        rec.exit_code = result["exit_code"]
        db.session.commit()
    return out_id


# --------------------------------------------------------------------------- #
# Handler da tool (chamado pelo loop do agente)
# --------------------------------------------------------------------------- #

# --- peças reutilizáveis (usadas pelo executar_shell e pelas automações) --- #

def shell_level(user_id: int) -> str | None:
    """Nível de controle de shell do usuário: None | 'principal' | 'full'."""
    user = db.session.get(User, user_id)
    if user is None:
        return None
    if user.shell_full_control:
        return "full"
    if user.is_principal:
        return "principal"
    return None


def check_budget() -> str | None:
    """Consome 1 do orçamento de shell por turno. Erro (str) se estourou."""
    n = _shell_count.get() + 1
    _shell_count.set(n)
    if n > current_app.config["MAX_SHELL_PER_TURN"]:
        return "limite de comandos por turno atingido; peça ao usuário para continuar depois."
    return None


def run_direct(user_id: int, command: str) -> dict:
    """Executa JÁ (sem card) e persiste a saída no chat + trilha de auditoria."""
    result = run_shell(command)
    _persist_output(user_id, command, result)
    from app import audit
    audit.record(user_id, "shell", command, result["exit_code"])
    return {
        "ok": True,
        "executed": True,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def create_pending(user_id: int, command: str, motivo: str = "") -> dict:
    """Cria o pedido de aprovação (card no chat) e devolve o sinal pending."""
    with write_lock:
        rec = ShellCommand(user_id=user_id, command=command, status="pending")
        db.session.add(rec)
        db.session.flush()
        cmd_id = rec.id
        req = Message(
            user_id=user_id,
            role="assistant",
            content="Quero rodar um comando na sua máquina — você autoriza?",
            tool_name="shell_request",
            media_meta={"command": command, "cmd_id": cmd_id, "status": "pending", "motivo": motivo},
        )
        db.session.add(req)
        db.session.flush()
        rec.request_msg_id = req.id
        db.session.commit()
    return {
        "ok": True,
        "pending_approval": True,
        "info": "Pedido de permissão enviado ao usuário. Aguarde a decisão dele; não prossiga sozinho.",
    }


def executar_shell(user_id: int, args: dict) -> dict:
    cmd = (args.get("comando") or args.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "error": "comando vazio"}

    level = shell_level(user_id)
    if level is None:
        return {
            "ok": False,
            "error": (
                "Este usuário não tem permissão para executar comandos no "
                "computador. Explique gentilmente que só o usuário principal "
                "pode pedir isso, e não tente rodar nada."
            ),
        }
    budget_err = check_budget()
    if budget_err:
        return {"ok": False, "error": budget_err}

    # controle absoluto ou comando exato já confiado ("permitir sempre") → roda direto
    trusted = level == "full" or (
        db.session.query(ShellApproval)
        .filter_by(user_id=user_id, command=cmd)
        .first()
        is not None
    )
    if trusted:
        return run_direct(user_id, cmd)
    # comando novo → pede permissão e PARA (o loop trata `pending_approval`)
    return create_pending(user_id, cmd, (args.get("motivo") or "").strip())
