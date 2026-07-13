"""Resumo rolante da conversa (1 por usuário)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ConversationSummary(db.Model):
    __tablename__ = "conversation_summary"

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    summary = db.Column(db.Text, nullable=False, default="")
    # ponteiro da última mensagem já incorporada ao resumo
    last_summarized_msg_id = db.Column(db.Integer, nullable=True)
    updated_at = db.Column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "summary": self.summary,
            "last_summarized_msg_id": self.last_summarized_msg_id,
            "updated_at": self.updated_at.isoformat(),
        }
