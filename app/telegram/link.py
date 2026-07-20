"""Vínculo chat↔conta e a máquina de estado do login (email+senha).

O login espelha o do `helena chat`: valida a MESMA credencial (email+senha da
conta) e, em sucesso, grava um TelegramLink. O estado intermediário do login
(aguardando email/senha) é em memória — se o servidor reiniciar no meio, o
usuário só recomeça com /login.
"""
from __future__ import annotations

from sqlalchemy import func

from app.extensions import db, write_lock
from app.models import TelegramLink, User

# chat_id (str) -> {"stage": "email"|"password", "email": str}
_pending: dict[str, dict] = {}


# ---- vínculo persistido ---- #

def user_id_for_chat(chat_id) -> int | None:
    row = db.session.get(TelegramLink, str(chat_id))
    return row.user_id if row else None


def chats_for_user(user_id: int) -> list[str]:
    rows = db.session.query(TelegramLink).filter_by(user_id=user_id).all()
    return [r.chat_id for r in rows]


def link_chat(chat_id, user_id: int) -> None:
    with write_lock:
        row = db.session.get(TelegramLink, str(chat_id))
        if row is None:
            db.session.add(TelegramLink(chat_id=str(chat_id), user_id=user_id))
        else:
            row.user_id = user_id
        db.session.commit()


def unlink_chat(chat_id) -> bool:
    with write_lock:
        row = db.session.get(TelegramLink, str(chat_id))
        if row is None:
            return False
        db.session.delete(row)
        db.session.commit()
    return True


def authenticate(email: str, password: str) -> User | None:
    """Valida email+senha (mesma credencial da conta). None se inválido."""
    if not email or not password:
        return None
    user = (
        db.session.query(User)
        .filter(func.lower(User.email) == email.strip().lower())
        .first()
    )
    if user and user.check_password(password):
        return user
    return None


# ---- estado do fluxo de login (em memória) ---- #

def start_login(chat_id) -> None:
    _pending[str(chat_id)] = {"stage": "email"}


def cancel_login(chat_id) -> None:
    _pending.pop(str(chat_id), None)


def login_stage(chat_id) -> str | None:
    st = _pending.get(str(chat_id))
    return st["stage"] if st else None


def set_login_email(chat_id, email: str) -> None:
    _pending[str(chat_id)] = {"stage": "password", "email": email.strip()}


def pop_login_email(chat_id) -> str | None:
    st = _pending.pop(str(chat_id), None)
    return st.get("email") if st else None
