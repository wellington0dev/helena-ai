"""Perfil de gostos/rotina/metas que evolui ao longo das conversas (1 por usuário)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserProfile(db.Model):
    __tablename__ = "user_profile"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # estrutura semântica: nome_preferido, gostos, rotina, metas,
    # estilo_comunicacao, referencias_que_curte (ver CLAUDE.md §3)
    profile = db.Column(db.JSON, default=dict, nullable=False)
    updated_at = db.Column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "updated_at": self.updated_at.isoformat(),
        }
