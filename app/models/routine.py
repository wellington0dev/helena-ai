"""Listas/rotinas: sequência ORDENADA de passos que a Helena executa em ordem.

Cada passo é concreto (executável no handler, deterministicamente):
  {"kind": "command", "value": "<nome de um SavedCommand>"}  # referência
  {"kind": "shell",   "value": "<comando shell direto>"}

`created_by` segue a mesma regra de procedência do SavedCommand.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Routine(db.Model):
    __tablename__ = "routines"
    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_routine_user_name"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    steps = db.Column(db.JSON, default=list, nullable=False)  # [{"kind","value"}, ...]
    created_by = db.Column(db.Text, nullable=False, default="user")  # user | ai
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps or [],
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
