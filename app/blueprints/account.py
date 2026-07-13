"""Conta: info, preferências e as 3 ações destrutivas do §4."""
import shutil
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db, write_lock
from app.models import (
    AiNote, ConversationSummary, Job, Message, NotificationQueue,
    Reminder, Routine, SavedCommand, ShellApproval, ShellCommand, User, UserProfile,
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
    data = user.to_dict()
    prof = db.session.get(UserProfile, user.id)
    data["name"] = (prof.profile or {}).get("nome_preferido") if prof else None
    return jsonify(user=data), 200


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
            SavedCommand, Routine,
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
