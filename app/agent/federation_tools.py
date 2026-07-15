"""Tools do agente principal de chat pra federação — Fase 3.

Duas ações de INICIATIVA (task_share / help_request) e uma de DESCOBERTA
(list_federation_peers, só leitura). Nenhuma tool aqui pode produzir
kind="help_response" — isso é reservado ao humano (send_peer_message com
reply_to_message_id) e ao pipeline isolado _federation_reply
(worker.py::_complete_federation_reply). Ver nota de invariante no plano de
Fase 3: quem compõe a mensagem aqui é o agente PRIVILEGIADO (tem
UserProfile/AiNote/tools no contexto) — é um canal real de saída de dado
pra outra instância, por decisão da IA, só aceitável porque roda dentro de
um turno de chat ativo, exige ai_can_initiate + trust_level=="confiavel", e
nunca é silenciosa (notificação em sucesso + WebSocket + auditoria sempre).
"""
import uuid

from flask import current_app
from google.genai import types

from app import audit
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.federation.client import FederationError, send_message
from app.federation.util import is_paused
from app.models import NotificationQueue, Peer, PeerMessage
from app.realtime import emit_peer_message

LIST_FEDERATION_PEERS_DECL = types.FunctionDeclaration(
    name="list_federation_peers",
    description=(
        "Lista os peers federados (outras instâncias da Helena) já pareados, "
        "com id, nome, confiança e se a IA pode iniciar contato. Use ANTES de "
        "compartilhar resultado ou pedir ajuda a um peer — você precisa do "
        "peer_id certo."
    ),
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

FEDERATION_SHARE_RESULT_DECL = types.FunctionDeclaration(
    name="federation_share_result",
    description=(
        "Compartilha com um peer federado o resultado de algo que você acabou "
        "de fazer ou descobrir NESTA conversa. Só funciona se o usuário marcou "
        "esse peer como confiável E ligou 'IA pode iniciar contato' para ele — "
        "senão a tool devolve ok=false com o motivo; avise o usuário e sugira "
        "ajustar em Rede se ele quiser habilitar isso. Não invente o que "
        "compartilhar; use algo que já foi produzido/descoberto na conversa."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "peer_id": types.Schema(type=types.Type.INTEGER, description="Id do peer (de list_federation_peers)."),
            "summary": types.Schema(type=types.Type.STRING, description="Resumo claro do que está sendo compartilhado."),
        },
        required=["peer_id", "summary"],
    ),
)

FEDERATION_ASK_PEER_DECL = types.FunctionDeclaration(
    name="federation_ask_peer",
    description=(
        "Formula um PEDIDO DE AJUDA estruturado a outro peer federado — uma "
        "pergunta que espera resposta futura. O pedido recebe um "
        "identificador; quando a resposta chegar (pode demorar, você não "
        "recebe na hora — ela cai na Rede do usuário, não volta pra esta "
        "conversa automaticamente), ela é ligada ao pedido, mas só se a "
        "correlação bater com o que você mandou. Mesma restrição de "
        "confiança/consentimento de federation_share_result."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "peer_id": types.Schema(type=types.Type.INTEGER, description="Id do peer (de list_federation_peers)."),
            "question": types.Schema(type=types.Type.STRING, description="A pergunta, clara e autocontida."),
        },
        required=["peer_id", "question"],
    ),
)

FEDERATION_INITIATE_DECLS = [
    LIST_FEDERATION_PEERS_DECL, FEDERATION_SHARE_RESULT_DECL, FEDERATION_ASK_PEER_DECL,
]


def list_federation_peers(user_id: int, _args: dict) -> dict:
    rows = db.session.query(Peer).filter_by(user_id=user_id).all()
    return {
        "ok": True,
        "peers": [
            {
                "peer_id": p.id, "label": p.label, "trust_level": p.trust_level,
                "ai_can_initiate": p.ai_can_initiate,
            }
            for p in rows
        ],
    }


def _notify_title(label: str, kind: str) -> str:
    if kind == "task_share":
        return f"Sua IA compartilhou algo com {label}"
    if kind == "help_request":
        return f"Sua IA pediu ajuda a {label}"
    return f"Sua IA mandou algo para {label}"


def _send_ai_initiated(user_id: int, peer_id, body: str, kind: str) -> dict:
    if is_paused(user_id):
        return {"ok": False, "error": "federação pausada (modo pânico) — reative em Rede antes"}

    peer = db.session.get(Peer, peer_id)
    if peer is None or peer.user_id != user_id:
        return {"ok": False, "error": "peer não encontrado"}
    if not peer.ai_can_initiate:
        return {"ok": False, "error": f"você não autorizou a IA a iniciar contato com {peer.label} — ative isso em Rede se quiser"}
    if peer.trust_level != "confiavel":
        return {"ok": False, "error": f"{peer.label} não está marcado como confiável — iniciativa da IA exige confiança total"}

    cooldown = current_app.config["FEDERATION_AI_INITIATE_COOLDOWN_SECONDS"]
    if peer.ai_initiate_last_at is not None:
        elapsed = (now_utc() - peer.ai_initiate_last_at).total_seconds()
        if elapsed < cooldown:
            wait = int(cooldown - elapsed)
            return {"ok": False, "error": f"já falei com {peer.label} por conta própria há pouco — espera cerca de {wait}s antes de tentar de novo"}

    request_id = uuid.uuid4().hex if kind == "help_request" else None

    with write_lock:
        # relê dentro do lock pra fechar a janela de corrida do cooldown entre
        # a checagem acima e este commit (duas tool calls quase simultâneas).
        peer = db.session.get(Peer, peer_id)
        if peer.ai_initiate_last_at is not None:
            elapsed = (now_utc() - peer.ai_initiate_last_at).total_seconds()
            if elapsed < cooldown:
                db.session.rollback()
                return {"ok": False, "error": f"já falei com {peer.label} por conta própria há pouco"}
        msg = PeerMessage(
            peer_id=peer.id, user_id=user_id, direction="outgoing", body=body,
            status="pending", authored_by="ai", kind=kind, request_id=request_id,
        )
        db.session.add(msg)
        peer.ai_initiate_last_at = now_utc()
        db.session.commit()
        msg_id = msg.id
        label = peer.label

    try:
        send_message(peer, body, kind=kind, request_id=request_id)
        ok = True
    except FederationError as exc:
        ok = False
        current_app.logger.warning("federação: falha ao enviar iniciativa da IA pro peer %s: %s", peer_id, exc)

    with write_lock:
        msg = db.session.get(PeerMessage, msg_id)
        msg.status = "sent" if ok else "failed"
        if ok:
            # notificação só em sucesso — em falha o título "compartilhou"/
            # "pediu ajuda" seria enganoso; o status=failed já aparece na
            # thread via emit_peer_message abaixo, visibilidade mantida sem
            # alegar êxito.
            db.session.add(NotificationQueue(
                user_id=user_id, title=_notify_title(label, kind), body=body[:500],
                fire_at=now_utc(), type="peer_message", reference_id=msg_id,
            ))
        db.session.commit()
        out = msg.to_dict()

    audit.record(
        user_id, "federation",
        f"IA {'compartilhou resultado com' if kind == 'task_share' else 'pediu ajuda a'} "
        f"{label}: {'ok' if ok else 'falhou'}",
    )
    emit_peer_message(user_id, out)  # sempre — sucesso ou falha, nunca silencioso

    if not ok:
        return {"ok": False, "error": "não consegui entregar a mensagem a esse peer agora", "message_id": msg_id}
    result = {"ok": True, "message_id": msg_id}
    if kind == "help_request":
        result["request_id"] = request_id
        result["note"] = "a resposta pode demorar; ela chega como notificação/mensagem nova depois, não agora."
    return result


def federation_share_result(user_id: int, args: dict) -> dict:
    peer_id = args.get("peer_id")
    body = (args.get("summary") or "").strip()
    if not peer_id or not body:
        return {"ok": False, "error": "peer_id e summary são obrigatórios"}
    return _send_ai_initiated(user_id, peer_id, body, "task_share")


def federation_ask_peer(user_id: int, args: dict) -> dict:
    peer_id = args.get("peer_id")
    body = (args.get("question") or "").strip()
    if not peer_id or not body:
        return {"ok": False, "error": "peer_id e question são obrigatórios"}
    return _send_ai_initiated(user_id, peer_id, body, "help_request")


FEDERATION_INITIATE_HANDLERS = {
    "list_federation_peers": list_federation_peers,
    "federation_share_result": federation_share_result,
    "federation_ask_peer": federation_ask_peer,
}
