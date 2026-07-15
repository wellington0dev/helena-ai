"""Helpers de federação compartilhados entre o blueprint e as tools do agente."""
from app.extensions import db
from app.models import User


def is_paused(user_id: int) -> bool:
    user = db.session.get(User, user_id)
    return bool(user and user.federation_paused)
