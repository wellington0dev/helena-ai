"""Mensagem trocada com um peer federado — texto puro (Fase 1, zero IA)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PeerMessage(db.Model):
    __tablename__ = "peer_messages"

    id = db.Column(db.Integer, primary_key=True)
    peer_id = db.Column(
        db.Integer, db.ForeignKey("peers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # denormalizado (além do peer.user_id) pra reusar _delete_where() no wipe
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction = db.Column(db.Text, nullable=False)  # incoming | outgoing
    body = db.Column(db.Text, nullable=False)
    status = db.Column(db.Text, nullable=False, default="pending")  # pending|sent|failed|received
    # Fase 2: quem escreveu esta mensagem de SAÍDA (human|ai). Mensagens de
    # entrada ficam sempre no default "human" — nunca confiamos na autoria
    # que o peer alega sobre a própria mensagem.
    authored_by = db.Column(db.Text, nullable=False, default="human")
    # Fase 3: tipo estrutural da mensagem.
    #   chat          — texto livre (Fases 1/2, comportamento default/legado)
    #   task_share    — IA compartilhou o resultado de algo, por iniciativa
    #   help_request  — pedido de ajuda estruturado (iniciativa OU recebido)
    #   help_response — resposta a um help_request (humana ou pipeline isolado)
    kind = db.Column(db.Text, nullable=False, default="chat")
    # SÓ confiável quando fomos NÓS que geramos (outgoing, kind=help_request:
    # uuid4 local). Em mensagem INCOMING kind=help_request é o namespace do
    # PEER — guardado só pra relay futuro, nunca usado pra validar nada do
    # nosso lado.
    request_id = db.Column(db.Text, nullable=True, index=True)
    # Valor de correlação CRU vindo do wire — em incoming é um CLAIM do peer
    # (não confiável por si só); ver verified_request_message_id abaixo.
    in_reply_to = db.Column(db.Text, nullable=True, index=True)
    # Preenchido SÓ server-side (nunca a partir de payload do peer), SÓ em
    # mensagens INCOMING kind=help_response, e SÓ depois de casar
    # in_reply_to com um request_id que existe numa PeerMessage de SAÍDA
    # NOSSA (mesmo peer_id). É o ÚNICO campo que UI/notificação podem usar
    # pra afirmar "isto responde ao seu pedido X" (app/blueprints/federation.py::webhook_message).
    verified_request_message_id = db.Column(
        db.Integer, db.ForeignKey("peer_messages.id", ondelete="SET NULL"), nullable=True
    )
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "peer_id": self.peer_id,
            "direction": self.direction,
            "body": self.body,
            "status": self.status,
            "authored_by": self.authored_by,
            "kind": self.kind,
            "request_id": self.request_id,
            "in_reply_to": self.in_reply_to,
            "verified_request_message_id": self.verified_request_message_id,
            "created_at": self.created_at.isoformat(),
        }
