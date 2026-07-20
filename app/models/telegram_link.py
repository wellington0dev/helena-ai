"""Vínculo entre um chat do Telegram e uma conta da Helena.

Cada `chat_id` do Telegram (1:1 com uma conversa) aponta pra um `user_id`. Um
mesmo usuário pode ter vários chats vinculados (vários aparelhos). O login é
feito pelo bot com email+senha (mesma credencial do `helena chat`); aqui só
guardamos o resultado do vínculo.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TelegramLink(db.Model):
    __tablename__ = "telegram_links"

    # chat_id do Telegram como string (cabe qualquer tamanho, é a chave natural)
    chat_id = db.Column(db.Text, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
