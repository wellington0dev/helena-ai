"""Comandos exatos que o usuário confiou ("permitir sempre" = só o comando idêntico).

Um `executar_shell` cujo comando (string exata) esteja aqui roda sem pedir
permissão de novo. Qualquer comando diferente volta a pedir aprovação.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShellApproval(db.Model):
    __tablename__ = "shell_approvals"
    __table_args__ = (db.UniqueConstraint("user_id", "command", name="uq_user_command"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command = db.Column(db.Text, nullable=False)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
