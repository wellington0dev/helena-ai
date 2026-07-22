"""Decisão do usuário sobre comandos de shell que a Helena quer rodar.

`POST /commands/<id>/decision` {decision: allow|deny|always}. Integridade:
- JWT + dono do comando; o comando executado é SEMPRE o gravado na linha (nunca
  vem do cliente — o cliente só manda a decisão);
- claim atômico pending→running/denied sob write_lock: um double-tap/replay não
  executa duas vezes;
- allow/always executa; always memoriza o comando EXATO (permitir sempre);
- deny e allow re-invocam o agente para a Helena reagir ao resultado.
"""
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.agent import runner, shell_tool
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.models import Message, ShellApproval, ShellCommand

commands_bp = Blueprint("commands", __name__, url_prefix="/commands")


def _mark_request(msg_id: int | None, status: str) -> None:
    """Atualiza a msg de pedido de permissão (some com os botões no reload)."""
    if not msg_id:
        return
    with write_lock:
        msg = db.session.get(Message, msg_id)
        if msg is not None:
            meta = dict(msg.media_meta or {})
            meta["status"] = status
            msg.media_meta = meta
            db.session.commit()


def _remember(user_id: int, command: str, target_host: str | None = None) -> None:
    """`target_host` vira '' (não NULL) no registro — ver comentário no model
    ShellApproval sobre por que NULL não serve pra unicidade aqui."""
    host = target_host or ""
    if (
        db.session.query(ShellApproval)
        .filter_by(user_id=user_id, command=command, target_host=host)
        .first()
        is None
    ):
        with write_lock:
            db.session.add(ShellApproval(user_id=user_id, command=command, target_host=host))
            db.session.commit()


def _persist_tool(user_id: int, content: str, tool_name: str) -> int:
    with write_lock:
        msg = Message(user_id=user_id, role="tool", content=content, tool_name=tool_name)
        db.session.add(msg)
        db.session.commit()
        return msg.id


@commands_bp.get("/approvals")
@jwt_required()
def list_approvals():
    """Comandos que o usuário confiou ('permitir sempre'). Para revisar/revogar."""
    uid = int(get_jwt_identity())
    rows = (
        db.session.query(ShellApproval)
        .filter_by(user_id=uid)
        .order_by(ShellApproval.id.desc())
        .all()
    )
    return jsonify(
        approvals=[
            {"id": r.id, "command": r.command, "created_at": r.created_at.isoformat()}
            for r in rows
        ]
    ), 200


@commands_bp.delete("/approvals/<int:approval_id>")
@jwt_required()
def revoke_approval(approval_id: int):
    """Revoga um 'permitir sempre' — o comando volta a pedir permissão."""
    uid = int(get_jwt_identity())
    with write_lock:
        row = db.session.get(ShellApproval, approval_id)
        if row is None or row.user_id != uid:
            return jsonify(error="não encontrado"), 404
        db.session.delete(row)
        db.session.commit()
    return jsonify(ok=True), 200


def apply_shell_decision(user_id: int, cmd_id: int, decision: str):
    """Núcleo da decisão de shell, reusável (HTTP e Telegram). Faz o claim
    atômico, executa/nega, re-invoca o agente e faz o fan-out (emit_new_messages,
    que também entrega ao Telegram). Devolve (messages|None, error|None, status)."""
    if decision not in ("allow", "deny", "always"):
        return None, "decision deve ser allow|deny|always", 400

    # claim atômico: só age se ainda pending e do dono
    with write_lock:
        rec = db.session.get(ShellCommand, cmd_id)
        if rec is None or rec.user_id != user_id:
            return None, "comando não encontrado", 404
        if rec.status != "pending":
            return None, "comando já foi decidido", 409
        rec.status = "denied" if decision == "deny" else "running"
        rec.decided_at = now_utc()
        db.session.commit()
        command = rec.command
        target_host = rec.target_host
        req_msg_id = rec.request_msg_id

    _mark_request(req_msg_id, "denied" if decision == "deny" else "allowed")

    if decision == "deny":
        deny_text = (
            f"O usuário NEGOU a execução via SSH em {target_host}: {command}"
            if target_host else
            f"O usuário NEGOU a execução do comando: {command}"
        )
        since_id = _persist_tool(user_id, deny_text, "ssh_denied" if target_host else "shell_denied")
    else:
        if decision == "always":
            _remember(user_id, command, target_host)
        # execução real (comando = o gravado, nunca do cliente)
        since_id = shell_tool.execute_recorded(rec)

    # re-invoca o agente para a Helena reagir ao resultado/negativa
    runner.handle_user_turn(user_id, since_id)

    # coleta as mensagens novas desde o pedido (saída + respostas da Helena)
    new_msgs = (
        db.session.query(Message)
        .filter(Message.user_id == user_id, Message.id >= since_id)
        .order_by(Message.id.asc())
        .all()
    )
    dicts = [m.to_dict() for m in new_msgs]

    from app.realtime import emit_new_messages

    emit_new_messages(user_id, dicts)
    return dicts, None, 200


@commands_bp.post("/<int:cmd_id>/decision")
@jwt_required()
def decide(cmd_id: int):
    user_id = int(get_jwt_identity())
    decision = (request.get_json(silent=True) or {}).get("decision")
    messages, error, status = apply_shell_decision(user_id, cmd_id, decision)
    if error:
        return jsonify(error=error), status
    return jsonify(messages=messages), status
