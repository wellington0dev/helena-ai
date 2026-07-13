"""Comandos de shell que a Helena quer executar — com aprovação do usuário.

Cada `executar_shell` que não seja de um comando já confiado vira uma linha aqui
com status `pending`, até o usuário decidir (allow/deny/always) pelo chat.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShellCommand(db.Model):
    __tablename__ = "shell_commands"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command = db.Column(db.Text, nullable=False)
    # pending | running | done | error | denied
    status = db.Column(db.Text, nullable=False, default="pending", index=True)
    stdout = db.Column(db.Text, nullable=True)
    stderr = db.Column(db.Text, nullable=True)
    exit_code = db.Column(db.Integer, nullable=True)
    request_msg_id = db.Column(db.Integer, nullable=True)  # msg de pedido de permissão
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
    decided_at = db.Column(UtcDateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at.isoformat(),
        }
