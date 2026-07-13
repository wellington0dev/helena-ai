"""Registro e login (JWT multi-usuário)."""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import create_access_token
from sqlalchemy import select

from app.extensions import db, write_lock
from app.models import User, UserProfile

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _user_payload(user: User) -> dict:
    """user.to_dict() + o nome preferido (guardado no user_profile)."""
    data = user.to_dict()
    prof = db.session.get(UserProfile, user.id)
    data["name"] = (prof.profile or {}).get("nome_preferido") if prof else None
    return data


@auth_bp.post("/register")
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip() or None
    name = (data.get("name") or "").strip() or None

    if not username or not password:
        return jsonify(error="username e password são obrigatórios"), 400
    if len(password) < 6:
        return jsonify(error="password precisa de ao menos 6 caracteres"), 400

    with write_lock:
        exists = db.session.scalar(select(User).where(User.username == username))
        if exists:
            return jsonify(error="username já existe"), 409
        if email and db.session.scalar(select(User).where(User.email == email)):
            return jsonify(error="email já existe"), 409

        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()  # garante user.id
        if name:
            # já deixa o nome no perfil, pra Helena chamar o usuário assim
            db.session.add(UserProfile(user_id=user.id, profile={"nome_preferido": name}))
        db.session.commit()

    token = create_access_token(identity=str(user.id))
    return jsonify(access_token=token, user=_user_payload(user)), 201


@auth_bp.post("/login")
def login():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = db.session.scalar(select(User).where(User.username == username))
    if not user or not user.check_password(password):
        return jsonify(error="credenciais inválidas"), 401

    token = create_access_token(identity=str(user.id))
    return jsonify(access_token=token, user=_user_payload(user)), 200
