"""Lembretes — agenda (3 etapas: 1w/1d/6h) ou simples (1 disparo)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Reminder(db.Model):
    __tablename__ = "reminders"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title = db.Column(db.Text, nullable=False)
    description = db.Column(db.Text, nullable=True)
    due_at = db.Column(UtcDateTime, nullable=False, index=True)  # quando o evento acontece

    origin = db.Column(db.Text, nullable=False, default="user")  # user | ai
    kind = db.Column(db.Text, nullable=False, default="simple")  # agenda | simple
    # recorrência (só p/ simple): None | daily | weekly | monthly | yearly
    recurrence = db.Column(db.Text, nullable=True)

    # agenda: 3 etapas obrigatórias
    notified_1w = db.Column(db.Boolean, default=False, nullable=False)
    notified_1d = db.Column(db.Boolean, default=False, nullable=False)
    notified_6h = db.Column(db.Boolean, default=False, nullable=False)

    # simple: disparo único
    notify_at = db.Column(UtcDateTime, nullable=True)
    notified = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "due_at": self.due_at.isoformat(),
            "origin": self.origin,
            "kind": self.kind,
            "recurrence": self.recurrence,
            "notified_1w": self.notified_1w,
            "notified_1d": self.notified_1d,
            "notified_6h": self.notified_6h,
            "notify_at": self.notify_at.isoformat() if self.notify_at else None,
            "notified": self.notified,
            "created_at": self.created_at.isoformat(),
        }
