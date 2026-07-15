"""Federação Helena-a-Helena — Fase 1: pareamento + transporte assinado +
mensagem de texto simples entre usuários. ZERO participação de IA aqui —
isto é infraestrutura de confiança/transporte, não uma tool do agente.

Duas famílias de rotas:
- autenticadas por JWT (gerenciar peers, mandar/ler mensagens);
- públicas (`/federation/pairing/redeem`, `/federation/webhook/message`) —
  autenticadas por código de uso único (pareamento) ou HMAC por peer
  (mensagem), nunca por JWT — são chamadas por OUTRO servidor, não pelo app.
"""
from datetime import timedelta

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from app import audit
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.federation import crypto
from app.federation.client import FederationError, redeem_pairing_code, send_message
from app.federation.util import is_paused
from app.jobs import worker
from app.models import Job, NotificationQueue, PairingCode, Peer, PeerMessage, PeerNonce, User, UserProfile
from app.models.peer import TRUST_LEVELS
from app.realtime import emit_peer_message, emit_peer_paired

federation_bp = Blueprint("federation", __name__)


def _uid() -> int:
    return int(get_jwt_identity())


def _owned(model, item_id: int, uid: int):
    obj = db.session.get(model, item_id)
    return obj if obj and obj.user_id == uid else None


def _display_name(uid: int) -> str:
    prof = db.session.get(UserProfile, uid)
    name = (prof.profile or {}).get("nome_preferido") if prof else None
    if name:
        return name
    user = db.session.get(User, uid)
    if user:
        return user.name or user.email or "alguém"
    return "alguém"


# --------------------------------------------------------------------------- #
# JWT — configurações e kill-switch local
# --------------------------------------------------------------------------- #

@federation_bp.get("/federation/settings")
@jwt_required()
def get_settings():
    uid = _uid()
    return jsonify(
        public_url_configured=bool(current_app.config["FEDERATION_PUBLIC_URL"]),
        paused=is_paused(uid),
    ), 200


@federation_bp.post("/federation/resume")
@jwt_required()
def resume():
    """Reativa a federação depois de um panic (nada revoga isto automaticamente)."""
    uid = _uid()
    with write_lock:
        user = db.session.get(User, uid)
        if user is not None:
            user.federation_paused = False
        db.session.commit()
    return jsonify(ok=True), 200


# --------------------------------------------------------------------------- #
# JWT — pareamento (metade local: gerar código / resgatar código de outrem)
# --------------------------------------------------------------------------- #

@federation_bp.post("/federation/peers/pairing-codes")
@jwt_required()
def create_pairing_code():
    uid = _uid()
    if is_paused(uid):
        return jsonify(error="federação pausada (modo pânico) — reative antes"), 403

    code = crypto.generate_pairing_code()
    ttl = current_app.config["FEDERATION_PAIRING_TTL_SECONDS"]
    expires_at = now_utc() + timedelta(seconds=ttl)
    with write_lock:
        db.session.add(
            PairingCode(user_id=uid, code_hash=crypto.hash_code(code), expires_at=expires_at)
        )
        db.session.commit()
    return jsonify(code=code, expires_at=expires_at.isoformat()), 201


@federation_bp.post("/federation/peers")
@jwt_required()
def redeem_peer_code():
    """Resgata um código gerado por OUTRA instância — chamada de saída."""
    uid = _uid()
    if is_paused(uid):
        return jsonify(error="federação pausada (modo pânico) — reative antes"), 403

    my_public_url = current_app.config["FEDERATION_PUBLIC_URL"]
    if not my_public_url:
        return jsonify(error="configure FEDERATION_PUBLIC_URL no servidor antes de parear"), 400

    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    base_url = (data.get("base_url") or "").strip()
    if not code or not base_url:
        return jsonify(error="code e base_url são obrigatórios"), 400

    url_err = crypto.validate_peer_url(base_url)
    if url_err:
        return jsonify(error=url_err), 400

    try:
        result = redeem_pairing_code(base_url, code, my_public_url, _display_name(uid))
    except FederationError as exc:
        return jsonify(error=str(exc)), 502

    link_id = result.get("link_id")
    shared_secret = result.get("shared_secret")
    label = result.get("label") or base_url
    if not link_id or not shared_secret:
        return jsonify(error="resposta de pareamento incompleta"), 502

    with write_lock:
        peer = Peer(
            user_id=uid, link_id=link_id, shared_secret=shared_secret,
            remote_base_url=base_url, label=label, trust_level="a_averiguar",
        )
        db.session.add(peer)
        db.session.commit()
        out = peer.to_dict()
    audit.record(uid, "federation", f"pareado com {label} ({base_url})")
    return jsonify(peer=out), 201


# --------------------------------------------------------------------------- #
# JWT — CRUD de peers + mensagens
# --------------------------------------------------------------------------- #

@federation_bp.get("/federation/peers")
@jwt_required()
def list_peers():
    rows = db.session.query(Peer).filter_by(user_id=_uid()).order_by(Peer.created_at.desc()).all()
    return jsonify(peers=[p.to_dict() for p in rows]), 200


@federation_bp.put("/federation/peers/<int:pid>")
@jwt_required()
def update_peer(pid: int):
    uid = _uid()
    peer = _owned(Peer, pid, uid)
    if peer is None:
        return jsonify(error="não encontrado"), 404
    data = request.get_json(silent=True) or {}
    with write_lock:
        if "label" in data and (data.get("label") or "").strip():
            peer.label = data["label"].strip()
        if "trust_level" in data:
            level = data.get("trust_level")
            if level not in TRUST_LEVELS:
                return jsonify(error=f"trust_level deve ser um de {list(TRUST_LEVELS)}"), 400
            peer.trust_level = level
        if "ai_dialogue_enabled" in data:
            peer.ai_dialogue_enabled = bool(data.get("ai_dialogue_enabled"))
        if "ai_can_initiate" in data:
            peer.ai_can_initiate = bool(data.get("ai_can_initiate"))
        if peer.trust_level != "confiavel":
            # a UI já acopla os dois campos, mas reforça aqui pra não deixar
            # o backend num estado inconsistente se chamado direto pela API
            # (o gate de runtime em federation_tools já bloqueava o envio de
            # qualquer forma — isto só evita o badge "IA pode iniciar" mentir).
            peer.ai_can_initiate = False
        db.session.commit()
        out = peer.to_dict()
    return jsonify(peer=out), 200


@federation_bp.delete("/federation/peers/<int:pid>")
@jwt_required()
def delete_peer(pid: int):
    peer = _owned(Peer, pid, _uid())
    if peer is None:
        return jsonify(error="não encontrado"), 404
    with write_lock:
        db.session.delete(peer)
        db.session.commit()
    return jsonify(ok=True), 200


@federation_bp.get("/federation/peers/<int:pid>/messages")
@jwt_required()
def list_peer_messages(pid: int):
    uid = _uid()
    peer = _owned(Peer, pid, uid)
    if peer is None:
        return jsonify(error="não encontrado"), 404
    rows = (
        db.session.query(PeerMessage)
        .filter_by(peer_id=pid, user_id=uid)
        .order_by(PeerMessage.created_at.asc())
        .all()
    )
    return jsonify(messages=[m.to_dict() for m in rows]), 200


@federation_bp.post("/federation/peers/<int:pid>/messages")
@jwt_required()
def send_peer_message(pid: int):
    uid = _uid()
    if is_paused(uid):
        return jsonify(error="federação pausada (modo pânico) — reative antes"), 403
    peer = _owned(Peer, pid, uid)
    if peer is None:
        return jsonify(error="não encontrado"), 404
    data = request.get_json(silent=True) or {}
    body = (data.get("body") or "").strip()
    if not body:
        return jsonify(error="body obrigatório"), 400

    kind, in_reply_to = "chat", None
    reply_to_id = data.get("reply_to_message_id")
    if reply_to_id is not None:
        origin = (
            db.session.query(PeerMessage)
            .filter_by(id=reply_to_id, peer_id=pid, user_id=uid,
                       direction="incoming", kind="help_request")
            .first()
        )
        if origin is None:
            return jsonify(error="mensagem original não encontrada ou não é um pedido de ajuda"), 400
        kind, in_reply_to = "help_response", origin.request_id

    # grava PENDING e libera o lock ANTES da chamada de rede — nunca segurar
    # write_lock durante um HTTP de saída (serializaria todo escritor do banco)
    with write_lock:
        msg = PeerMessage(peer_id=pid, user_id=uid, direction="outgoing", body=body,
                           status="pending", kind=kind, in_reply_to=in_reply_to)
        db.session.add(msg)
        # um humano se envolveu — zera o teto de respostas automáticas seguidas,
        # independente do resultado da entrega abaixo.
        peer.ai_turn_streak = 0
        db.session.commit()
        msg_id = msg.id

    try:
        send_message(peer, body, kind=kind, in_reply_to=in_reply_to)
        ok = True
    except FederationError as exc:
        ok = False
        current_app.logger.warning("federação: falha ao enviar pro peer %s: %s", peer.id, exc)

    with write_lock:
        msg = db.session.get(PeerMessage, msg_id)
        msg.status = "sent" if ok else "failed"
        db.session.commit()
        out = msg.to_dict()
    audit.record(uid, "federation", f"mensagem para {peer.label}: {'ok' if ok else 'falhou'}")
    if not ok:
        return jsonify(message=out, error="falha ao entregar a mensagem"), 502
    return jsonify(message=out), 201


# --------------------------------------------------------------------------- #
# PÚBLICAS — chamadas por OUTRO servidor, nunca pelo app
# --------------------------------------------------------------------------- #

@federation_bp.post("/federation/pairing/redeem")
def pairing_redeem():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    peer_base_url = (data.get("peer_base_url") or "").strip()
    peer_label = (data.get("label") or "").strip() or peer_base_url
    if not code or not peer_base_url:
        return jsonify(error="code e peer_base_url são obrigatórios"), 400

    url_err = crypto.validate_peer_url(peer_base_url)
    if url_err:
        return jsonify(error=url_err), 400

    code_hash = crypto.hash_code(code)
    with write_lock:
        pc = db.session.query(PairingCode).filter_by(code_hash=code_hash).first()
        if pc is None or pc.used or pc.expires_at < now_utc():
            db.session.rollback()
            return jsonify(error="código inválido ou expirado"), 404
        if is_paused(pc.user_id):
            db.session.rollback()
            return jsonify(error="federação pausada nesta instância"), 403

        pc.used = True
        pc.used_at = now_utc()

        link_id = crypto.generate_link_id()
        shared_secret = crypto.generate_shared_secret()
        peer = Peer(
            user_id=pc.user_id, link_id=link_id, shared_secret=shared_secret,
            remote_base_url=peer_base_url, label=peer_label, trust_level="a_averiguar",
        )
        db.session.add(peer)
        db.session.flush()  # popula peer.id pro reference_id da notificação

        db.session.add(
            _notification_for(
                pc.user_id, title="Novo par vinculado",
                body=f"{peer_label} vinculou o Helena dele ao seu.",
                type_="peer_paired", reference_id=peer.id,
            )
        )
        db.session.commit()
        owner_id = pc.user_id
        peer_out = peer.to_dict()

    audit.record(owner_id, "federation", f"pareamento recebido de {peer_label}")
    emit_peer_paired(owner_id, peer_out)
    return jsonify(link_id=link_id, shared_secret=shared_secret, label=_display_name(owner_id)), 201


def _notification_for(user_id: int, title: str, body: str, type_: str, reference_id: int):
    return NotificationQueue(
        user_id=user_id, title=title, body=body[:500], fire_at=now_utc(),
        type=type_, reference_id=reference_id,
    )


_VALID_KINDS = {"chat", "task_share", "help_request", "help_response"}


def _incoming_title(label: str, kind: str, *, verified: bool) -> str:
    if kind == "task_share":
        return f"{label} compartilhou algo com você"
    if kind == "help_request":
        return f"{label} pediu ajuda"
    if kind == "help_response":
        return f"{label} respondeu ao seu pedido" if verified else f"Resposta de {label}"
    return f"Mensagem de {label}"


@federation_bp.post("/federation/webhook/message")
def webhook_message():
    link_id = request.headers.get("X-Helena-Link-Id")
    timestamp = request.headers.get("X-Helena-Timestamp")
    nonce = request.headers.get("X-Helena-Nonce")
    signature = request.headers.get("X-Helena-Signature")
    if not all([link_id, timestamp, nonce, signature]):
        return jsonify(error="headers de assinatura ausentes"), 400

    window = current_app.config["FEDERATION_REPLAY_WINDOW_SECONDS"]
    if not crypto.timestamp_fresh(timestamp, window):
        return jsonify(error="timestamp fora da janela"), 401

    raw_body = request.get_data(cache=True)

    peer = db.session.query(Peer).filter_by(link_id=link_id).first()
    if peer is None:
        return jsonify(error="não autorizado"), 401

    if not crypto.verify(peer.shared_secret, "POST", request.path, timestamp, nonce, raw_body, signature):
        return jsonify(error="não autorizado"), 401

    with write_lock:
        db.session.add(PeerNonce(peer_id=peer.id, nonce=nonce))
        try:
            db.session.flush()
        except IntegrityError:
            db.session.rollback()
            return jsonify(error="replay detectado"), 401

        if is_paused(peer.user_id):
            db.session.commit()  # mantém o nonce consumido mesmo pausado
            return jsonify(error="federação pausada nesta instância"), 403

        data = request.get_json(silent=True) or {}
        body_text = (data.get("body") or "").strip()
        if not body_text:
            db.session.rollback()
            return jsonify(error="body obrigatório"), 400

        kind = data.get("kind")
        if kind not in _VALID_KINDS:
            kind = "chat"  # peer malformado ou de versão futura — nunca persiste enum desconhecido

        raw_request_id = (data.get("request_id") or "").strip() or None
        raw_in_reply_to = (data.get("in_reply_to") or "").strip() or None

        verified_request_message_id = None
        if kind == "help_response" and raw_in_reply_to:
            origin = (
                db.session.query(PeerMessage)
                .filter_by(peer_id=peer.id, direction="outgoing",
                           kind="help_request", request_id=raw_in_reply_to)
                .first()
            )
            if origin is not None:
                verified_request_message_id = origin.id
            # não achou → in_reply_to cru ainda é guardado (auditoria), mas
            # verified_request_message_id fica None. A UI/título de
            # notificação NUNCA afirma "responde ao seu pedido X" sem isso —
            # fecha o caso de um peer B reivindicar o request_id de um
            # pedido que mandamos pro peer A (escopo por peer_id).

        msg = PeerMessage(
            peer_id=peer.id, user_id=peer.user_id, direction="incoming",
            body=body_text, status="received", kind=kind,
            request_id=raw_request_id if kind == "help_request" else None,
            in_reply_to=raw_in_reply_to if kind == "help_response" else None,
            verified_request_message_id=verified_request_message_id,
        )
        db.session.add(msg)
        db.session.flush()

        db.session.add(
            _notification_for(
                peer.user_id,
                title=_incoming_title(peer.label, kind, verified=verified_request_message_id is not None),
                body=body_text, type_="peer_message", reference_id=msg.id,
            )
        )
        db.session.commit()
        owner_id = peer.user_id
        msg_out = msg.to_dict()

    audit.record(owner_id, "federation", f"mensagem recebida de {peer.label}")
    emit_peer_message(owner_id, msg_out)

    # Fase 2: enfileira uma resposta automática da IA, se o usuário habilitou
    # isso pra este peer. Checagem é um limite suave (ver nota em worker.py) —
    # a chamada de rede acontece dentro do job assíncrono, nunca aqui.
    peer = db.session.get(Peer, peer.id)  # relê fresco (fora do lock anterior)
    if (
        peer is not None
        and peer.ai_dialogue_enabled
        and peer.trust_level != "nao_confiavel"
        and not is_paused(owner_id)
        and peer.ai_turn_streak < current_app.config["FEDERATION_MAX_AI_TURNS"]
    ):
        with write_lock:
            db.session.add(
                Job(user_id=owner_id, type="federation_reply",
                    payload={"peer_id": peer.id, "message_id": msg_out["id"]}, status="pending")
            )
            db.session.commit()
        worker.request_wake()

    return jsonify(ok=True), 200
