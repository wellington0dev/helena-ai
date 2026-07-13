"""Anotações que a IA cria via tool (contexto, fatos, preferências, pendências)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AiNote(db.Model):
    __tablename__ = "ai_notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content = db.Column(db.Text, nullable=False)
    category = db.Column(db.Text, nullable=True)  # contexto|fato|preferencia|pendencia
    tags = db.Column(db.JSON, default=list, nullable=False)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "category": self.category,
            "tags": self.tags,
            "created_at": self.created_at.isoformat(),
        }
