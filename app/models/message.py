"""Mensagens do chat único (uma conversa por usuário)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Message(db.Model):
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role = db.Column(db.Text, nullable=False)  # user | assistant | system | tool
    content = db.Column(db.Text, nullable=False, default="")

    # Mídia (estilo WhatsApp): guarda arquivo + meta, nunca substitui pela transcrição
    media_url = db.Column(db.Text, nullable=True)
    media_type = db.Column(db.Text, nullable=True)  # image|audio|document|spreadsheet|pdf
    media_meta = db.Column(db.JSON, nullable=True)  # duration, mime, transcript, etc.

    tool_name = db.Column(db.Text, nullable=True)  # se role=tool
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "media_url": self.media_url,
            "media_type": self.media_type,
            "media_meta": self.media_meta,
            "tool_name": self.tool_name,
            "created_at": self.created_at.isoformat(),
        }
