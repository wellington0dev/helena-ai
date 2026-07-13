"""Chat único por usuário — histórico e envio de mensagens (texto-only por ora)."""
from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func, select

from app.agent import runner
from app.extensions import db, write_lock
from app.media import ingest, storage
from app.models import Message

chat_bp = Blueprint("chat", __name__, url_prefix="/messages")


def _current_user_id() -> int:
    return int(get_jwt_identity())


@chat_bp.get("")
@jwt_required()
def list_messages():
    """Histórico paginado (mais recentes primeiro via ?before=<id>)."""
    user_id = _current_user_id()
    limit = min(int(request.args.get("limit", 50)), 200)
    before = request.args.get("before", type=int)

    stmt = select(Message).where(Message.user_id == user_id)
    if before:
        stmt = stmt.where(Message.id < before)
    stmt = stmt.order_by(Message.id.desc()).limit(limit)

    rows = db.session.scalars(stmt).all()
    rows.reverse()  # devolve em ordem cronológica
    return jsonify(messages=[m.to_dict() for m in rows]), 200


@chat_bp.get("/info")
@jwt_required()
def chat_info():
    """Dados da conversa (perfil estilo WhatsApp): início, total e mídias."""
    user_id = _current_user_id()

    started_at = db.session.scalar(
        select(func.min(Message.created_at)).where(Message.user_id == user_id)
    )
    total = db.session.scalar(
        select(func.count(Message.id)).where(Message.user_id == user_id)
    )
    # mídias compartilhadas (mais recentes primeiro), limitadas
    media_rows = db.session.scalars(
        select(Message)
        .where(Message.user_id == user_id, Message.media_url.is_not(None))
        .order_by(Message.id.desc())
        .limit(120)
    ).all()

    return jsonify(
        started_at=started_at.isoformat() if started_at else None,
        total_messages=total or 0,
        media=[m.to_dict() for m in media_rows],
    ), 200


@chat_bp.post("")
@jwt_required()
def send_message():
    """Persiste a mensagem do usuário (texto e/ou mídia), roda o agente e devolve
    as respostas. Aceita `content` e opcionalmente `media_url`/`media_type`
    (obtidos antes via POST /media/upload)."""
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    media_url = data.get("media_url")
    media_type = data.get("media_type")

    if not content and not media_url:
        return jsonify(error="mensagem vazia (sem content nem mídia)"), 400

    # valida a mídia: precisa existir e pertencer ao usuário
    media_meta = None
    if media_url:
        if storage.owner_of(media_url) not in (None, user_id):
            return jsonify(error="mídia de outro usuário"), 403
        if storage.resolve(user_id, media_url) is None:
            return jsonify(error="mídia não encontrada"), 404
        media_meta = data.get("media_meta") or {}
        # ingest: transcreve áudio / descreve imagem uma vez
        media_meta = ingest.process(user_id, media_type, media_url, media_meta)

    with write_lock:
        user_msg = Message(
            user_id=user_id,
            role="user",
            content=content,
            media_url=media_url,
            media_type=media_type,
            media_meta=media_meta,
        )
        db.session.add(user_msg)
        db.session.commit()
        user_dict = user_msg.to_dict()
        since_id = user_msg.id

    if not current_app.config.get("GEMINI_API_KEY"):
        return jsonify(error="GEMINI_API_KEY não configurada"), 503

    replies = runner.handle_user_turn(user_id, since_id)
    reply_dicts = [m.to_dict() for m in replies]

    # empurra para o app aberto (§13). Essencial para respostas vindas da
    # notificação (RemoteInput), que não passam pela WebView. Dedupe no cliente.
    from app.realtime import emit_new_messages

    emit_new_messages(user_id, [user_dict, *reply_dicts])
    return jsonify(message=user_dict, replies=reply_dicts), 201
