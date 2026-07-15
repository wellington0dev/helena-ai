"""Conta: info, preferências e as 3 ações destrutivas do §4."""
import shutil
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db, write_lock
from app.models import (
    AiNote, AuditEntry, ConversationSummary, Job, Message, NotificationQueue,
    PairingCode, Peer, PeerMessage, Reminder, Routine, SavedCommand,
    ShellApproval, ShellCommand, User, UserProfile,
)

account_bp = Blueprint("account", __name__, url_prefix="/account")


def _uid() -> int:
    return int(get_jwt_identity())


def _delete_where(model, user_id: int) -> None:
    db.session.query(model).filter(model.user_id == user_id).delete(
        synchronize_session=False
    )


@account_bp.get("/me")
@jwt_required()
def me():
    user = db.session.get(User, _uid())
    return jsonify(user=user.to_dict()), 200


@account_bp.put("/basic-info")
@jwt_required()
def update_basic_info():
    """Edita nome/email da CONTA. Distinto de PUT /account/name (que edita
    nome_preferido — como a Helena chama o usuário, conceito separado)."""
    data = request.get_json(silent=True) or {}
    uid = _uid()
    with write_lock:
        user = db.session.get(User, uid)
        # valida ANTES de mutar qualquer campo — evita deixar o objeto ORM
        # sujo (não commitado) na sessão se a request falhar no meio.
        new_email = None
        if "email" in data:
            new_email = (data.get("email") or "").strip().lower() or None
            if new_email and db.session.query(User).filter(User.email == new_email, User.id != uid).first():
                return jsonify(error="email já existe"), 409
        if "name" in data:
            user.name = (data.get("name") or "").strip() or None
        if "email" in data:
            user.email = new_email
        db.session.commit()
        result = user.to_dict()
    return jsonify(ok=True, user=result), 200


@account_bp.put("/name")
@jwt_required()
def update_name():
    """Atualiza o nome preferido do usuário (como a Helena o chama)."""
    name = (request.get_json(silent=True) or {}).get("name")
    name = (name or "").strip()
    if not name:
        return jsonify(error="name obrigatório"), 400
    uid = _uid()
    with write_lock:
        prof = db.session.get(UserProfile, uid)
        if prof is None:
            prof = UserProfile(user_id=uid, profile={})
            db.session.add(prof)
        prof.profile = {**(prof.profile or {}), "nome_preferido": name}
        db.session.commit()
    return jsonify(ok=True, name=name), 200


@account_bp.put("/notif-prefs")
@jwt_required()
def notif_prefs():
    prefs = request.get_json(silent=True) or {}
    with write_lock:
        user = db.session.get(User, _uid())
        user.notif_prefs = prefs
        db.session.commit()
        result = user.notif_prefs
    return jsonify(notif_prefs=result), 200


@account_bp.get("/browsers")
@jwt_required()
def list_browsers():
    """Navegadores instalados nesta máquina + qual está configurado como padrão."""
    from app.agent import browsers

    user = db.session.get(User, _uid())
    return jsonify(installed=browsers.detect_browsers(), default=user.default_browser), 200


@account_bp.put("/browsers/default")
@jwt_required()
def set_default_browser():
    from app.agent import browsers

    browser_id = (request.get_json(silent=True) or {}).get("browser_id") or None
    if browser_id is not None:
        installed_ids = {b["id"] for b in browsers.detect_browsers()}
        if browser_id not in installed_ids:
            return jsonify(error="navegador não encontrado/instalado"), 400
    with write_lock:
        user = db.session.get(User, _uid())
        user.default_browser = browser_id
        db.session.commit()
        result = user.default_browser
    return jsonify(ok=True, default=result), 200


@account_bp.get("/audit")
@jwt_required()
def audit():
    """Trilha do que a Helena executou na máquina (mais recentes primeiro)."""
    uid = _uid()
    limit = min(int(request.args.get("limit", 100)), 500)
    rows = (
        db.session.query(AuditEntry)
        .filter_by(user_id=uid)
        .order_by(AuditEntry.created_at.desc())
        .limit(limit)
        .all()
    )
    return jsonify(entries=[e.to_dict() for e in rows]), 200


@account_bp.post("/panic")
@jwt_required()
def panic():
    """Kill switch: revoga TODAS as permissões deste usuário e nega comandos
    pendentes — a Helena para de poder mexer na máquina na hora."""
    uid = _uid()
    with write_lock:
        user = db.session.get(User, uid)
        if user is not None:
            user.is_principal = False
            user.shell_full_control = False
            user.federation_paused = True
        db.session.query(ShellCommand).filter_by(user_id=uid, status="pending").update(
            {ShellCommand.status: "denied"}, synchronize_session=False
        )
        db.session.commit()
    return jsonify(ok=True, message="permissões revogadas"), 200


@account_bp.post("/reset-chat")
@jwt_required()
def reset_chat():
    """Recomeçar chat: apaga só as mensagens (mantém contexto e agenda)."""
    uid = _uid()
    with write_lock:
        _delete_where(Message, uid)
        db.session.commit()
    return jsonify(ok=True, action="reset-chat"), 200


@account_bp.post("/reset-context")
@jwt_required()
def reset_context():
    """Apagar chat + contexto: mensagens, resumo, notas e perfil.
    Mantém os lembretes (compromissos reais) — §4."""
    uid = _uid()
    with write_lock:
        _delete_where(Message, uid)
        _delete_where(ConversationSummary, uid)
        _delete_where(AiNote, uid)
        _delete_where(UserProfile, uid)
        db.session.commit()
    return jsonify(ok=True, action="reset-context"), 200


@account_bp.post("/wipe")
@jwt_required()
def wipe():
    """Limpar dados da conta: TUDO do user_id + arquivos de mídia no disco.
    Body {deleteAccount: bool} decide se a conta em si também é removida."""
    uid = _uid()
    delete_account = bool((request.get_json(silent=True) or {}).get("deleteAccount"))
    with write_lock:
        for model in (
            Message, ConversationSummary, AiNote, UserProfile,
            Reminder, Job, NotificationQueue, ShellCommand, ShellApproval,
            SavedCommand, Routine, AuditEntry,
            Peer, PeerMessage, PairingCode,
        ):
            _delete_where(model, uid)
        if delete_account:
            user = db.session.get(User, uid)
            if user:
                db.session.delete(user)
        db.session.commit()

    # remove arquivos de mídia no disco (§15) — não só linhas do banco
    media_dir = Path(current_app.config["MEDIA_DIR"]) / str(uid)
    shutil.rmtree(media_dir, ignore_errors=True)

    return jsonify(ok=True, action="wipe", account_deleted=delete_account), 200
