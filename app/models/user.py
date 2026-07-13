"""Model de usuário (multi-usuário, auth JWT)."""
from datetime import datetime, timezone

import bcrypt

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, unique=True, nullable=False, index=True)
    email = db.Column(db.Text, unique=True, nullable=True)
    password_hash = db.Column(db.Text, nullable=False)
    push_registered = db.Column(db.Boolean, default=False, nullable=False)
    notif_prefs = db.Column(db.JSON, default=dict, nullable=False)
    # usuário principal: só ele pode pedir para a Helena executar comandos no PC
    is_principal = db.Column(db.Boolean, default=False, nullable=False)
    # controle absoluto: executa QUALQUER comando sem pedir aprovação (implica principal)
    shell_full_control = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)

    def set_password(self, raw: str) -> None:
        self.password_hash = bcrypt.hashpw(
            raw.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    def check_password(self, raw: str) -> bool:
        return bcrypt.checkpw(
            raw.encode("utf-8"), self.password_hash.encode("utf-8")
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "push_registered": self.push_registered,
            "notif_prefs": self.notif_prefs,
            "created_at": self.created_at.isoformat(),
        }
