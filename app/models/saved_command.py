"""Comandos salvos: atalhos de shell nomeados que a Helena pode executar.

`created_by` guarda a procedência (segurança): comandos criados PELO USUÁRIO na
página são pré-aprovados (rodam sem card); os criados PELA IA via tool ainda
passam pelo card de aprovação ao executar.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SavedCommand(db.Model):
    __tablename__ = "saved_commands"
    __table_args__ = (db.UniqueConstraint("user_id", "name", name="uq_cmd_user_name"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    command = db.Column(db.Text, nullable=False)  # shell (pode ter várias linhas/&&)
    created_by = db.Column(db.Text, nullable=False, default="user")  # user | ai
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "command": self.command,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
