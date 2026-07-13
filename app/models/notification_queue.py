"""Fila de notificações pré-calculada pelo servidor, puxada pelo app (offline-first)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class NotificationQueue(db.Model):
    __tablename__ = "notification_queue"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.Text, nullable=False)
    body = db.Column(db.Text, nullable=False)  # texto gerado pela IA
    fire_at = db.Column(
        UtcDateTime, nullable=False, index=True
    )  # quando o celular dispara localmente
    type = db.Column(db.Text, nullable=False)  # reminder|ai_initiative|job_done
    reference_id = db.Column(db.Integer, nullable=True)  # reminder_id / job_id
    delivered = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "fire_at": self.fire_at.isoformat(),
            "type": self.type,
            "reference_id": self.reference_id,
            "delivered": self.delivered,
            "created_at": self.created_at.isoformat(),
        }
