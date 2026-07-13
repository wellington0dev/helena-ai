"""Fila de background works (pesquisa, plano, geração de doc/imagem...)."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(db.Model):
    __tablename__ = "jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type = db.Column(db.Text, nullable=False)  # research|plan|generate_doc|generate_image|...
    payload = db.Column(db.JSON, default=dict, nullable=False)
    status = db.Column(
        db.Text, nullable=False, default="pending", index=True
    )  # pending|running|done|error
    result_ref = db.Column(db.Text, nullable=True)  # msg_id ou caminho do resultado
    error = db.Column(db.Text, nullable=True)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
    updated_at = db.Column(
        UtcDateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "status": self.status,
            "result_ref": self.result_ref,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
