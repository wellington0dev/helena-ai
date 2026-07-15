"""Peer: vínculo bilateral pareado com outra instância da Helena (federação).

`shared_secret` nunca deve ser serializado em `to_dict()` — é usado só no
backend pra assinar/verificar requests HMAC (server/app/federation/crypto.py).
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime

TRUST_LEVELS = ("confiavel", "nao_confiavel", "a_averiguar")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Peer(db.Model):
    __tablename__ = "peers"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    link_id = db.Column(db.Text, unique=True, nullable=False, index=True)
    shared_secret = db.Column(db.Text, nullable=False)
    remote_base_url = db.Column(db.Text, nullable=False)
    label = db.Column(db.Text, nullable=False)
    trust_level = db.Column(db.Text, nullable=False, default="a_averiguar")
    # Fase 2: opt-in de resposta automática da IA + contador interno de segurança
    # (nunca exposto em to_dict() como número — só o toggle é estado de UI).
    ai_dialogue_enabled = db.Column(db.Boolean, nullable=False, default=False)
    ai_turn_streak = db.Column(db.Integer, nullable=False, default=0)
    # Fase 3: DISTINTO de ai_dialogue_enabled. ai_dialogue_enabled = "posso
    # auto-RESPONDER quando esse peer me manda algo" (pipeline isolado).
    # ai_can_initiate = "minha IA pode INICIAR contato com esse peer por
    # conta própria, dentro de um turno de chat" — decisão de confiança mais
    # forte, pois quem compõe a mensagem é o agente PRIVILEGIADO. Só tem
    # efeito com trust_level=="confiavel" (checado em runtime, não só na UI).
    ai_can_initiate = db.Column(db.Boolean, nullable=False, default=False)
    # Throttle PRÓPRIO da iniciativa da IA, independente de ai_turn_streak
    # (que é só sobre a cadeia de RESPOSTAS automáticas). Não exposto em
    # to_dict() — bookkeeping interno, mesmo espírito de ai_turn_streak.
    ai_initiate_last_at = db.Column(UtcDateTime, nullable=True)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "link_id": self.link_id,
            "remote_base_url": self.remote_base_url,
            "label": self.label,
            "trust_level": self.trust_level,
            "ai_dialogue_enabled": self.ai_dialogue_enabled,
            "ai_can_initiate": self.ai_can_initiate,
            "created_at": self.created_at.isoformat(),
        }
