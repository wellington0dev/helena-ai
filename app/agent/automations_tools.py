"""Tools de automação: comandos salvos + listas/rotinas, e busca web (pesquisar).

Execução respeita a PROCEDÊNCIA (segurança):
- entidade criada pelo USUÁRIO (na página) → pré-aprovada: roda sem card;
- criada pela IA (via tool) → passa pelo card de aprovação (a menos que o usuário
  esteja em controle absoluto, onde nada pede card).

Listas rodam EM ORDEM, inteiras, no handler (determinístico) — só passos concretos
(ref a comando salvo ou shell direto). Refs quebradas são puladas com aviso.
"""
import unicodedata

from google.genai import types

from app.agent import shell_tool
from app.agent.loop_tools import LOOP_TOOLS, _pesquisar
from app.agenda import scan
from app.agenda.timeutil import parse_due
from app.extensions import db, write_lock
from app.models import Routine, SavedCommand


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").strip().lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _find_command(user_id: int, name: str) -> SavedCommand | None:
    n = _norm(name)
    for c in db.session.query(SavedCommand).filter_by(user_id=user_id).all():
        if _norm(c.name) == n:
            return c
    return None


def _find_routine(user_id: int, name: str) -> Routine | None:
    n = _norm(name)
    for r in db.session.query(Routine).filter_by(user_id=user_id).all():
        if _norm(r.name) == n:
            return r
    return None


def _build_script(user_id: int, routine: Routine) -> tuple[str, list[str]]:
    """Resolve os passos → script (uma linha por passo) + refs não encontradas."""
    lines, missing = [], []
    for step in routine.steps or []:
        kind = step.get("kind")
        value = (step.get("value") or "").strip()
        if not value:
            continue
        if kind == "command":
            cmd = _find_command(user_id, value)
            if cmd:
                lines.append(cmd.command)
            else:
                missing.append(value)
        else:  # shell
            lines.append(value)
    return "\n".join(lines), missing


def _silent(level: str, created_by: str) -> bool:
    """Roda sem card? Controle absoluto sempre; senão só se o usuário autorou."""
    return level == "full" or created_by == "user"


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #

def executar_comando(user_id: int, args: dict) -> dict:
    name = args.get("nome") or args.get("name") or ""
    cmd = _find_command(user_id, name)
    if cmd is None:
        return {"ok": False, "error": f"comando salvo '{name}' não encontrado"}
    level = shell_tool.shell_level(user_id)
    if level is None:
        return {"ok": False, "error": "Sem permissão para executar comandos. Explique ao usuário."}
    err = shell_tool.check_budget()
    if err:
        return {"ok": False, "error": err}
    if _silent(level, cmd.created_by):
        return shell_tool.run_direct(user_id, cmd.command)
    return shell_tool.create_pending(user_id, cmd.command, f"comando salvo: {cmd.name}")


def executar_lista(user_id: int, args: dict) -> dict:
    name = args.get("nome") or args.get("name") or ""
    routine = _find_routine(user_id, name)
    if routine is None:
        return {"ok": False, "error": f"lista '{name}' não encontrada"}
    level = shell_tool.shell_level(user_id)
    if level is None:
        return {"ok": False, "error": "Sem permissão para executar comandos. Explique ao usuário."}
    script, missing = _build_script(user_id, routine)
    if not script.strip():
        return {"ok": False, "error": "a lista não tem passos executáveis"}
    err = shell_tool.check_budget()
    if err:
        return {"ok": False, "error": err}
    note = f" (refs puladas: {', '.join(missing)})" if missing else ""
    if _silent(level, routine.created_by):
        result = shell_tool.run_direct(user_id, script)
        result["info"] = f"executei a lista '{routine.name}'{note}"
        return result
    return shell_tool.create_pending(user_id, script, f"lista '{routine.name}'{note}")


def agendar_lista(user_id: int, args: dict) -> dict:
    routine = _find_routine(user_id, args.get("nome") or "")
    if routine is None:
        return {"ok": False, "error": "lista não encontrada"}
    if shell_tool.shell_level(user_id) is None:
        return {"ok": False, "error": "só usuário principal pode agendar (a lista rodaria sozinha)."}
    if routine.created_by != "user":
        return {
            "ok": False,
            "error": "só dá pra agendar listas que o USUÁRIO criou na página. Peça pra ele salvar como dele primeiro.",
        }
    try:
        nxt = parse_due(args["quando"])
    except (KeyError, ValueError):
        return {"ok": False, "error": "'quando' inválido (use ISO 8601 no horário local)"}
    rec = (args.get("recorrencia") or "").strip().lower() or None
    if rec == "none":
        rec = None
    if rec and rec not in scan.RECURRENCES:
        return {"ok": False, "error": f"recorrencia deve ser um de {sorted(scan.RECURRENCES)}"}
    with write_lock:
        routine.next_run = nxt
        routine.recurrence = rec
        routine.enabled = True
        db.session.commit()
    return {"ok": True, "agendada": routine.name, "next_run": nxt.isoformat(), "recorrencia": rec}


def desagendar_lista(user_id: int, args: dict) -> dict:
    routine = _find_routine(user_id, args.get("nome") or "")
    if routine is None:
        return {"ok": False, "error": "lista não encontrada"}
    with write_lock:
        routine.enabled = False
        db.session.commit()
    return {"ok": True, "desagendada": routine.name}


# --------------------------------------------------------------------------- #
# CRUD (via IA → created_by='ai')
# --------------------------------------------------------------------------- #

def salvar_comando(user_id: int, args: dict) -> dict:
    name = (args.get("nome") or "").strip()
    command = (args.get("comando") or "").strip()
    if not name or not command:
        return {"ok": False, "error": "nome e comando são obrigatórios"}
    desc = (args.get("descricao") or "").strip() or None
    with write_lock:
        c = _find_command(user_id, name)
        if c:
            c.command = command
            c.description = desc
        else:
            c = SavedCommand(user_id=user_id, name=name, description=desc,
                             command=command, created_by="ai")
            db.session.add(c)
        db.session.commit()
    return {"ok": True, "saved": name, "info": "comando salvo (você aprova ao executar)"}


def salvar_lista(user_id: int, args: dict) -> dict:
    name = (args.get("nome") or "").strip()
    if not name:
        return {"ok": False, "error": "nome obrigatório"}
    raw = args.get("passos") or []
    steps = []
    for p in raw:
        if isinstance(p, dict):
            kind = p.get("kind") if p.get("kind") in ("command", "shell") else "shell"
            steps.append({"kind": kind, "value": (p.get("value") or "").strip()})
        elif isinstance(p, str):
            steps.append({"kind": "shell", "value": p.strip()})
    steps = [s for s in steps if s["value"]]
    if not steps:
        return {"ok": False, "error": "passos vazios"}
    desc = (args.get("descricao") or "").strip() or None
    with write_lock:
        r = _find_routine(user_id, name)
        if r:
            r.steps = steps
            r.description = desc
        else:
            r = Routine(user_id=user_id, name=name, description=desc,
                        steps=steps, created_by="ai")
            db.session.add(r)
        db.session.commit()
    return {"ok": True, "saved": name, "steps": len(steps)}


def apagar_comando(user_id: int, args: dict) -> dict:
    c = _find_command(user_id, args.get("nome") or "")
    if c is None:
        return {"ok": False, "error": "comando não encontrado"}
    with write_lock:
        db.session.delete(c)
        db.session.commit()
    return {"ok": True, "deleted": c.name}


def apagar_lista(user_id: int, args: dict) -> dict:
    r = _find_routine(user_id, args.get("nome") or "")
    if r is None:
        return {"ok": False, "error": "lista não encontrada"}
    with write_lock:
        db.session.delete(r)
        db.session.commit()
    return {"ok": True, "deleted": r.name}


# --------------------------------------------------------------------------- #
# Declarações + dispatch
# --------------------------------------------------------------------------- #

_NAME = types.Schema(type=types.Type.OBJECT,
                     properties={"nome": types.Schema(type=types.Type.STRING)},
                     required=["nome"])

AUTOMATION_EXEC_DECLS = [  # exige principal (executam na máquina)
    types.FunctionDeclaration(
        name="executar_comando",
        description="Executa um COMANDO salvo pelo nome (veja a lista de comandos no contexto).",
        parameters=_NAME,
    ),
    types.FunctionDeclaration(
        name="executar_lista",
        description="Executa uma LISTA/rotina salva pelo nome — roda os passos em ordem. Use quando o usuário pedir para rodar uma rotina/lista dele.",
        parameters=_NAME,
    ),
    types.FunctionDeclaration(
        name="agendar_lista",
        description=(
            "Agenda uma lista para rodar SOZINHA num horário (e opcionalmente repetir). "
            "Ex.: 'roda meu modo trabalho toda manhã às 8h'. Só funciona com listas que o "
            "usuário criou na página dele."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "nome": types.Schema(type=types.Type.STRING),
                "quando": types.Schema(type=types.Type.STRING, description="1ª execução, ISO 8601 no horário local."),
                "recorrencia": types.Schema(
                    type=types.Type.STRING, enum=["daily", "weekly", "monthly", "yearly", "none"],
                    description="Repetição (omita/none = uma vez só).",
                ),
            },
            required=["nome", "quando"],
        ),
    ),
    types.FunctionDeclaration(
        name="desagendar_lista",
        description="Cancela o agendamento de uma lista (ela para de rodar sozinha).",
        parameters=_NAME,
    ),
]

AUTOMATION_MANAGE_DECLS = [  # disponíveis a todos (criar/apagar é inofensivo; busca web)
    types.FunctionDeclaration(
        name="salvar_comando",
        description="Cria/atualiza um comando salvo (atalho de shell) a pedido do usuário.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "nome": types.Schema(type=types.Type.STRING),
                "descricao": types.Schema(type=types.Type.STRING),
                "comando": types.Schema(type=types.Type.STRING, description="Comando(s) shell; use \\n ou && para vários."),
            },
            required=["nome", "comando"],
        ),
    ),
    types.FunctionDeclaration(
        name="salvar_lista",
        description="Cria/atualiza uma lista/rotina: passos ORDENADOS, cada um um comando shell ou referência a um comando salvo.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "nome": types.Schema(type=types.Type.STRING),
                "descricao": types.Schema(type=types.Type.STRING),
                "passos": types.Schema(
                    type=types.Type.ARRAY,
                    description="Passos em ordem.",
                    items=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            "kind": types.Schema(type=types.Type.STRING, enum=["command", "shell"],
                                                 description="command = nome de um comando salvo; shell = comando direto."),
                            "value": types.Schema(type=types.Type.STRING),
                        },
                        required=["kind", "value"],
                    ),
                ),
            },
            required=["nome", "passos"],
        ),
    ),
    types.FunctionDeclaration(name="apagar_comando", description="Apaga um comando salvo pelo nome.", parameters=_NAME),
    types.FunctionDeclaration(name="apagar_lista", description="Apaga uma lista/rotina pelo nome.", parameters=_NAME),
    # busca web (grounded). É uma function-tool de propósito: o Gemini NÃO permite
    # google_search junto com function-calling na mesma chamada, então a busca roda
    # numa chamada grounded isolada (loop_tools._pesquisar). Não "simplificar".
    LOOP_TOOLS.function_declarations[0],  # pesquisar(assunto)
]

AUTOMATION_HANDLERS = {
    "executar_comando": executar_comando,
    "executar_lista": executar_lista,
    "agendar_lista": agendar_lista,
    "desagendar_lista": desagendar_lista,
    "salvar_comando": salvar_comando,
    "salvar_lista": salvar_lista,
    "apagar_comando": apagar_comando,
    "apagar_lista": apagar_lista,
    "pesquisar": lambda user_id, args: _pesquisar(user_id, args),
}
