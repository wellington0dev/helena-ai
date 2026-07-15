"""Código de pareamento de curta duração — a metade "convite" da federação.

Guarda só o hash (sha256) do código; o texto puro é devolvido uma única vez
na criação e nunca fica persistido em lugar nenhum.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PairingCode(db.Model):
    __tablename__ = "pairing_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash = db.Column(db.Text, unique=True, nullable=False, index=True)
    expires_at = db.Column(UtcDateTime, nullable=False, index=True)
    used = db.Column(db.Boolean, default=False, nullable=False)
    used_at = db.Column(UtcDateTime, nullable=True)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
