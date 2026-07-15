"""Registro e login (JWT multi-usuário)."""
import re
import secrets

from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from sqlalchemy import func, select

from app.extensions import db, write_lock
from app.models import User, UserProfile

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

_USERNAME_SAFE_RE = re.compile(r"[^a-z0-9._-]+")


def _derive_username(email: str) -> str:
    """Base do username interno: parte local do email, saneada (sem '@').
    Nunca exposta na API — só satisfaz o UNIQUE NOT NULL legado da coluna."""
    local = email.split("@", 1)[0].lower()
    base = _USERNAME_SAFE_RE.sub("-", local).strip("-._")
    return (base or "user")[:40]


def _unique_username(base: str) -> str:
    """Chamar DENTRO do write_lock. Tenta o nome base; em colisão, sufixa."""
    if not db.session.scalar(select(User.id).where(User.username == base)):
        return base
    for _ in range(5):
        candidate = f"{base}-{secrets.token_hex(3)}"
        if not db.session.scalar(select(User.id).where(User.username == candidate)):
            return candidate
    return f"{base}-{secrets.token_hex(8)}"  # praticamente impossível colidir


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    # `username` do body é ignorado de propósito: não é mais parte do contrato.

    if not name or not email or not password:
        return jsonify(error="name, email e password são obrigatórios"), 400
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        return jsonify(error="email inválido"), 400
    if len(password) < 6:
        return jsonify(error="password precisa de ao menos 6 caracteres"), 400

    with write_lock:
        if db.session.scalar(select(User).where(func.lower(User.email) == email)):
            return jsonify(error="email já existe"), 409

        username = _unique_username(_derive_username(email))
        user = User(username=username, email=email, name=name)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # garante user.id
        # nome inicial de como a Helena se dirige ao usuário — evolui à parte
        # a partir daqui (PUT /account/name), independente de User.name.
        db.session.add(UserProfile(user_id=user.id, profile={"nome_preferido": name}))
        db.session.commit()
        out = user.to_dict()

    token = create_access_token(identity=str(user.id))
    return jsonify(access_token=token, user=out), 201


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    identifier = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not identifier or not password:
        return jsonify(error="email e password são obrigatórios"), 400

    # 1) email (case-insensitive); 2) fallback pra username legado (mesmo
    # campo do form — quem tem conta antiga digita o username ali mesmo).
    user = db.session.scalar(select(User).where(func.lower(User.email) == identifier.lower()))
    if user is None:
        user = db.session.scalar(select(User).where(User.username == identifier))

    if not user or not user.check_password(password):
        return jsonify(error="credenciais inválidas"), 401

    token = create_access_token(identity=str(user.id))
    return jsonify(access_token=token, user=user.to_dict()), 200
