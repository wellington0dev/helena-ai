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
import re
import shutil
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
        "sistema, etc. Roda no seu DIRETÓRIO DE TRABALHO atual (veja o contexto); "
        "use mudar_diretorio para navegar de forma persistente. Por segurança, o "
        "usuário PRECISA autorizar cada comando novo pelo chat — você NÃO deve "
        "assumir que rodou até receber a saída. Passe UM comando por chamada; "
        "encadeie com && se necessário. Adapte a sintaxe ao sistema operacional. "
        "Comandos com 'sudo' só funcionam se o usuário tiver habilitado sudo na "
        "Helena (helena users sudo); se não tiver, vai ser bloqueado — não tente "
        "contornar reescrevendo o comando."
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


MUDAR_DIRETORIO_DECL = types.FunctionDeclaration(
    name="mudar_diretorio",
    description=(
        "Muda o DIRETÓRIO DE TRABALHO atual, de forma persistente — todos os "
        "próximos comandos (executar_shell) e edições de código rodarão a partir "
        "dele até você mudar de novo. Use ao entrar num projeto/pasta para "
        "trabalhar. Aceita caminho absoluto, relativo ao diretório atual, ou '~'. "
        "Não precisa de aprovação (só navega, não executa nada)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "caminho": types.Schema(
                type=types.Type.STRING,
                description="Diretório de destino (absoluto, relativo ao atual, ou '~').",
            ),
        },
        required=["caminho"],
    ),
)


# --------------------------------------------------------------------------- #
# Diretório de trabalho (onde o shell roda e a Helena edita/cria código)
# --------------------------------------------------------------------------- #

def resolve_workdir(user_id: int) -> str:
    """Diretório de trabalho efetivo do usuário: `working_dir` se ainda existir
    como pasta, senão o home (fallback seguro). Nunca levanta."""
    user = db.session.get(User, user_id)
    wd = user.working_dir if user else None
    if wd:
        try:
            p = Path(wd)
            if p.is_dir():
                return str(p)
        except OSError:
            pass
    return str(Path.home())


def set_workdir(user_id: int, caminho: str) -> dict:
    """Muda o diretório de trabalho persistido, resolvendo relativo ao atual.
    Valida que o destino existe e é uma pasta. Devolve o resultado p/ o modelo."""
    raw = (caminho or "").strip()
    if not raw:
        return {"ok": False, "error": "caminho vazio"}
    base = Path(resolve_workdir(user_id))
    try:
        target = Path(raw).expanduser()
        if not target.is_absolute():
            target = base / target
        target = target.resolve()
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": f"caminho inválido: {exc}"}
    if not target.is_dir():
        return {"ok": False, "error": f"não é uma pasta existente: {target}"}
    with write_lock:
        user = db.session.get(User, user_id)
        if user is None:
            return {"ok": False, "error": "usuário não encontrado"}
        user.working_dir = str(target)
        db.session.commit()
    return {"ok": True, "working_dir": str(target)}


def mudar_diretorio(user_id: int, args: dict) -> dict:
    if shell_level(user_id) is None:
        return {
            "ok": False,
            "error": (
                "Este usuário não pode navegar/controlar o computador. "
                "Só o usuário principal pode."
            ),
        }
    return set_workdir(user_id, args.get("caminho") or args.get("path") or "")


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


def run_shell(command: str, cwd: str | None = None) -> dict:
    """Roda `command` no shell com os rails de segurança. `cwd` é o diretório de
    trabalho (default: home). Devolve {exit_code, stdout, stderr, timeout}.
    Nunca levanta."""
    cfg = current_app.config
    timeout = cfg["SHELL_TIMEOUT_SECONDS"]
    max_out = cfg["SHELL_MAX_OUTPUT"]
    workdir = cwd or str(Path.home())
    if not Path(workdir).is_dir():  # pasta pode ter sumido; cai no home
        workdir = str(Path.home())
    kwargs = dict(
        cwd=workdir,
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


def run_remote(host: str, command: str) -> dict:
    """Roda `command` em OUTRA máquina via SSH, usando as chaves/agente já
    configurados na conta do usuário que roda o servidor — nunca uma senha.
    Mesmo shape de retorno de `run_shell`. Nunca levanta."""
    cfg = current_app.config
    connect_timeout = cfg["SSH_CONNECT_TIMEOUT_SECONDS"]
    timeout = cfg["SSH_TIMEOUT_SECONDS"]
    max_out = cfg["SHELL_MAX_OUTPUT"]

    if not shutil.which("ssh"):
        return {"exit_code": None, "stdout": "", "stderr": "ssh não encontrado nesta máquina", "timeout": False}

    ssh_cmd = [
        "ssh",
        "-o", "BatchMode=yes",  # nunca pede senha/passphrase — falha na hora se não tiver chave
        "-o", f"ConnectTimeout={connect_timeout}",
        "-o", "StrictHostKeyChecking=accept-new",  # aceita host novo, mas rejeita chave que MUDOU
        host, "--", command,
    ]
    kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    )
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(ssh_cmd, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return {"exit_code": None, "stdout": "", "stderr": f"falha ao iniciar ssh: {exc}", "timeout": False}

    try:
        out, err = proc.communicate(timeout=connect_timeout + timeout)
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001
            out, err = "", ""
        err = (err or "") + f"\n[ssh morto após {timeout}s de timeout]"
        code, timed_out = None, True

    current_app.logger.info(
        "SSH exec (host=%s rc=%s%s): %s", host, code, " TIMEOUT" if timed_out else "", command
    )
    return {
        "exit_code": code,
        "stdout": _cap(out, max_out),
        "stderr": _cap(err, max_out),
        "timeout": timed_out,
    }


def _output_text(command: str, result: dict, target_host: str | None = None) -> str:
    """Texto da mensagem de saída mostrada no chat (bloco de terminal)."""
    prompt = f"$ ssh {target_host} -- {command}" if target_host else f"$ {command}"
    parts = [prompt]
    if result["stdout"]:
        parts.append(result["stdout"].rstrip())
    if result["stderr"].strip():
        parts.append("[stderr]\n" + result["stderr"].rstrip())
    rc = result["exit_code"]
    parts.append(f"[código de saída: {rc if rc is not None else 'timeout'}]")
    return "\n".join(parts)


def _persist_output(user_id: int, command: str, result: dict, target_host: str | None = None) -> int:
    """Mensagem de saída do comando (bloco de terminal, visível no chat)."""
    with write_lock:
        msg = Message(
            user_id=user_id,
            role="tool",
            content=_output_text(command, result, target_host),
            tool_name="ssh_output" if target_host else "shell_output",
            media_meta={"command": command, "target_host": target_host, "exit_code": result["exit_code"]},
        )
        db.session.add(msg)
        db.session.commit()
        return msg.id


def execute_recorded(rec: ShellCommand) -> int:
    """Executa um ShellCommand já claimado (status running) e persiste a saída.
    Atualiza status para done/error. Devolve o id da mensagem de saída (para o
    agente reagir a ela no re-invoke). `rec.target_host` preenchido → via SSH;
    vazio → shell local."""
    target_host = rec.target_host or None
    if target_host:
        result = run_remote(target_host, rec.command)
    else:
        result = run_shell(rec.command, resolve_workdir(rec.user_id))
    out_id = _persist_output(rec.user_id, rec.command, result, target_host)
    from app import audit
    kind = "ssh" if target_host else "shell"
    detail = f"{target_host}: {rec.command}" if target_host else rec.command
    audit.record(rec.user_id, kind, detail, result["exit_code"])
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


def is_approved(user_id: int, command: str, target_host: str | None = None) -> bool:
    """'Permitir sempre' — escopado por (comando, host). `target_host` é
    guardado como '' pra local (não NULL — ver comentário no model)."""
    return (
        db.session.query(ShellApproval)
        .filter_by(user_id=user_id, command=command, target_host=target_host or "")
        .first()
        is not None
    )


def run_direct(user_id: int, command: str, target_host: str | None = None) -> dict:
    """Executa JÁ (sem card) e persiste a saída no chat + trilha de auditoria."""
    if target_host:
        result = run_remote(target_host, command)
    else:
        result = run_shell(command, resolve_workdir(user_id))
    _persist_output(user_id, command, result, target_host)
    from app import audit
    kind = "ssh" if target_host else "shell"
    detail = f"{target_host}: {command}" if target_host else command
    audit.record(user_id, kind, detail, result["exit_code"])
    return {
        "ok": True,
        "executed": True,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }


def create_pending(user_id: int, command: str, motivo: str = "", target_host: str | None = None) -> dict:
    """Cria o pedido de aprovação (card no chat) e devolve o sinal pending."""
    content = (
        f"Quero rodar um comando via SSH em `{target_host}` — você autoriza?"
        if target_host else
        "Quero rodar um comando na sua máquina — você autoriza?"
    )
    with write_lock:
        rec = ShellCommand(user_id=user_id, command=command, target_host=target_host, status="pending")
        db.session.add(rec)
        db.session.flush()
        cmd_id = rec.id
        req = Message(
            user_id=user_id,
            role="assistant",
            content=content,
            tool_name="shell_request",
            media_meta={
                "command": command, "cmd_id": cmd_id, "status": "pending",
                "motivo": motivo, "target_host": target_host,
            },
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


_SUDO_RE = re.compile(r"\bsudo\b")


def _uses_sudo(command: str) -> bool:
    return bool(_SUDO_RE.search(command))


def _sudo_gate(user_id: int, command: str) -> tuple[bool, str | None]:
    """(bloqueado, motivo). Só olha comandos com sudo; o resto passa direto."""
    if not _uses_sudo(command):
        return False, None
    user = db.session.get(User, user_id)
    if user is None or not user.sudo_enabled:
        return True, (
            "Este comando usa sudo, mas o usuário não tem sudo habilitado na "
            "Helena. Explique isso e NÃO tente rodar — nem reescrever pra "
            "disfarçar a palavra 'sudo'. Ative com 'helena users sudo <email>'."
        )
    return False, None


def _sudo_forces_approval(user_id: int, command: str) -> bool:
    if not _uses_sudo(command):
        return False
    user = db.session.get(User, user_id)
    return bool(user and user.sudo_enabled and user.sudo_require_approval)


def _decide_and_dispatch(
    user_id: int, command: str, motivo: str = "", target_host: str | None = None
) -> dict:
    """Portão único de decisão (nível → orçamento → sudo → confiança), usado
    tanto pelo shell local quanto pelo SSH — assim nenhum dos dois pode
    esquecer de aplicar alguma dessas checagens."""
    level = shell_level(user_id)
    if level is None:
        return {
            "ok": False,
            "error": (
                "Este usuário não tem permissão para executar comandos remotos "
                "via SSH. Explique gentilmente que só o usuário principal pode "
                "pedir isso, e não tente rodar nada."
            ) if target_host else (
                "Este usuário não tem permissão para executar comandos no "
                "computador. Explique gentilmente que só o usuário principal "
                "pode pedir isso, e não tente rodar nada."
            ),
        }
    budget_err = check_budget()
    if budget_err:
        return {"ok": False, "error": budget_err}
    blocked, why = _sudo_gate(user_id, command)
    if blocked:
        return {"ok": False, "error": why}
    trusted = not _sudo_forces_approval(user_id, command) and (
        level == "full" or is_approved(user_id, command, target_host)
    )
    if trusted:
        return run_direct(user_id, command, target_host=target_host)
    return create_pending(user_id, command, motivo, target_host=target_host)


def executar_shell(user_id: int, args: dict) -> dict:
    cmd = (args.get("comando") or args.get("command") or "").strip()
    if not cmd:
        return {"ok": False, "error": "comando vazio"}
    motivo = (args.get("motivo") or "").strip()
    return _decide_and_dispatch(user_id, cmd, motivo)
