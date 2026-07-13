"""App factory da Helena."""
import logging

from flask import Flask, jsonify
from flask_cors import CORS

from app.config import Config
from app.extensions import db, jwt, socketio


def _register_jwt_callbacks() -> None:
    """Faz `@jwt_required()` retornar 401 quando o token é válido mas o usuário
    não existe mais (ex.: conta apagada). Sem isto, um token órfão dá histórico
    vazio + 500 no envio, em vez de mandar o app de volta ao login."""
    from app.models import User

    @jwt.user_lookup_loader
    def _load_user(_jwt_header, jwt_data):
        return db.session.get(User, int(jwt_data["sub"]))

    @jwt.user_lookup_error_loader
    def _user_not_found(_jwt_header, _jwt_data):
        return jsonify(error="sessão inválida"), 401


def create_app(config_object: type = Config) -> Flask:
    # roteia INFO do app para stderr (auditoria de shell/desktop cai no log).
    # basicConfig é no-op se já houver handlers, então garantimos o nível também.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.getLogger().setLevel(logging.INFO)

    app = Flask(__name__)
    app.config.from_object(config_object)
    app.logger.setLevel(logging.INFO)

    db.init_app(app)
    jwt.init_app(app)
    socketio.init_app(app)
    _register_jwt_callbacks()
    # CORS para a API REST (o browser faz preflight OPTIONS com Authorization)
    CORS(
        app,
        origins=app.config["CORS_ORIGINS"],
        allow_headers=["Authorization", "Content-Type"],
        supports_credentials=True,
    )

    # Importa models para registrá-los no metadata antes do create_all
    from app import models  # noqa: F401

    # registra os handlers do Socket.IO (connect/auth) no singleton
    from app import realtime  # noqa: F401

    from app.blueprints.account import account_bp
    from app.blueprints.auth import auth_bp
    from app.blueprints.chat import chat_bp
    from app.blueprints.commands import commands_bp
    from app.blueprints.library import library_bp
    from app.blueprints.media import media_bp
    from app.blueprints.reminders import reminders_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(reminders_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(commands_bp)
    app.register_blueprint(library_bp)

    with app.app_context():
        db.create_all()
        _ensure_columns()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def _ensure_columns() -> None:
    """Migração leve: adiciona colunas novas em bancos já existentes (SQLite não
    faz isso pelo create_all). Idempotente."""
    from sqlalchemy import inspect, text

    insp = inspect(db.engine)
    cols = {c["name"] for c in insp.get_columns("users")}
    if "is_principal" not in cols:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN is_principal BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    if "shell_full_control" not in cols:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN shell_full_control BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    rcols = {c["name"] for c in insp.get_columns("reminders")}
    if "recurrence" not in rcols:
        db.session.execute(text("ALTER TABLE reminders ADD COLUMN recurrence TEXT"))
        db.session.commit()
