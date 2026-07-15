"""Trilha de auditoria: tudo que a Helena EXECUTOU na máquina (shell/desktop).

Para o usuário ver e conferir o que foi feito em seu nome — visível no app e no
CLI. Complementa o log do servidor (sobrevive à rotação, é consultável e por-usuário).
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AuditEntry(db.Model):
    __tablename__ = "audit_entries"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind = db.Column(db.Text, nullable=False)  # shell | desktop
    detail = db.Column(db.Text, nullable=False)  # comando / ação
    exit_code = db.Column(db.Integer, nullable=True)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "detail": self.detail,
            "exit_code": self.exit_code,
            "created_at": self.created_at.isoformat(),
        }
