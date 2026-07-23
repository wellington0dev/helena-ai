"""App factory da Helena."""
import logging

from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

from app.config import Config
from app.extensions import db, jwt, socketio


def _register_jwt_callbacks() -> None:
    """Faz `@jwt_required()` retornar 401 quando o token é válido mas o usuário
    não existe mais (ex.: conta apagada). Sem isto, um token órfão dá histórico
    vazio + 500 no envio, em vez de mandar o app de volta ao login."""
    from app.agenda.timeutil import now_utc
    from app.extensions import write_lock
    from app.models import User

    @jwt.user_lookup_loader
    def _load_user(_jwt_header, jwt_data):
        user = db.session.get(User, int(jwt_data["sub"]))
        if user is not None:
            # "última vez visto" pro painel de desktop — best-effort, uma
            # requisição autenticada não pode falhar por causa disso.
            try:
                with write_lock:
                    user.last_seen_at = now_utc()
                    db.session.commit()
            except Exception:  # noqa: BLE001
                db.session.rollback()
        return user

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
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.library import library_bp
    from app.blueprints.media import media_bp
    from app.blueprints.reminders import reminders_bp
    from app.blueprints.settings import settings_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(reminders_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(commands_bp)
    app.register_blueprint(library_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(dashboard_bp)

    with app.app_context():
        db.create_all()
        _ensure_columns()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # página web (chat + configuração) — arquivo estático único, sem
    # template engine (a página fala com a API via fetch(), igual a
    # qualquer outro cliente REST — nada de dado do servidor é renderizado
    # no HTML, então não há superfície de template injection aqui)
    @app.get("/")
    def webui():
        return send_from_directory(app.static_folder, "index.html")

    # painel de desktop (Electron carrega essa rota numa janela nativa) —
    # mesmo espírito da "/" acima: arquivo estático puro, fala com a API
    # via fetch(), nada renderizado no servidor.
    @app.get("/dashboard")
    def dashboard_page():
        return send_from_directory(app.static_folder, "dashboard.html")

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
    if "default_browser" not in cols:
        db.session.execute(text("ALTER TABLE users ADD COLUMN default_browser TEXT"))
        db.session.commit()
    if "name" not in cols:
        db.session.execute(text("ALTER TABLE users ADD COLUMN name TEXT"))
        db.session.commit()
    if "working_dir" not in cols:
        db.session.execute(text("ALTER TABLE users ADD COLUMN working_dir TEXT"))
        db.session.commit()
    if "last_seen_at" not in cols:
        db.session.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME"))
        db.session.commit()
    if "sudo_enabled" not in cols:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN sudo_enabled BOOLEAN NOT NULL DEFAULT 0")
        )
        db.session.commit()
    if "sudo_require_approval" not in cols:
        db.session.execute(
            text("ALTER TABLE users ADD COLUMN sudo_require_approval BOOLEAN NOT NULL DEFAULT 1")
        )
        db.session.commit()
    # federação removida: a coluna órfã `federation_paused` é NOT NULL e, sem o
    # model preenchê-la, quebraria todo INSERT de usuário novo. Dropa se existir.
    if "federation_paused" in cols:
        db.session.execute(text("ALTER TABLE users DROP COLUMN federation_paused"))
        db.session.commit()
    rcols = {c["name"] for c in insp.get_columns("reminders")}
    if "recurrence" not in rcols:
        db.session.execute(text("ALTER TABLE reminders ADD COLUMN recurrence TEXT"))
        db.session.commit()
    if "routines" in insp.get_table_names():
        rtcols = {c["name"] for c in insp.get_columns("routines")}
        for col, ddl in (
            ("enabled", "ALTER TABLE routines ADD COLUMN enabled BOOLEAN NOT NULL DEFAULT 0"),
            ("next_run", "ALTER TABLE routines ADD COLUMN next_run DATETIME"),
            ("recurrence", "ALTER TABLE routines ADD COLUMN recurrence TEXT"),
        ):
            if col not in rtcols:
                db.session.execute(text(ddl))
                db.session.commit()
    if "notification_queue" in insp.get_table_names():
        nqcols = {c["name"] for c in insp.get_columns("notification_queue")}
        if "desktop_notified" not in nqcols:
            db.session.execute(
                text("ALTER TABLE notification_queue ADD COLUMN desktop_notified BOOLEAN NOT NULL DEFAULT 0")
            )
            db.session.commit()
    if "shell_commands" in insp.get_table_names():
        sccols = {c["name"] for c in insp.get_columns("shell_commands")}
        if "target_host" not in sccols:
            db.session.execute(text("ALTER TABLE shell_commands ADD COLUMN target_host TEXT"))
            db.session.commit()
    if "shell_approvals" in insp.get_table_names():
        sacols = {c["name"] for c in insp.get_columns("shell_approvals")}
        if "target_host" not in sacols:
            # SQLite não deixa trocar uma UNIQUE constraint com ALTER TABLE — o
            # índice de uma constraint de tabela é "automático" e não pode ser
            # dropado sozinho (testado: "index associated with UNIQUE or
            # PRIMARY KEY constraint cannot be dropped"). Único jeito é recriar
            # a tabela com o shape novo. `target_host` NOT NULL DEFAULT '' (não
            # NULL) de propósito: NULL não conta como "igual" a NULL numa
            # UNIQUE constraint, o que deixaria passar aprovações locais
            # duplicadas.
            db.session.execute(text("ALTER TABLE shell_approvals RENAME TO shell_approvals_old"))
            db.session.execute(text(
                "CREATE TABLE shell_approvals ("
                "id INTEGER PRIMARY KEY, "
                "user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                "command TEXT NOT NULL, "
                "target_host TEXT NOT NULL DEFAULT '', "
                "created_at DATETIME NOT NULL, "
                "CONSTRAINT uq_user_command_host UNIQUE (user_id, command, target_host))"
            ))
            db.session.execute(text(
                "INSERT INTO shell_approvals (id, user_id, command, target_host, created_at) "
                "SELECT id, user_id, command, '', created_at FROM shell_approvals_old"
            ))
            db.session.execute(text("DROP TABLE shell_approvals_old"))
            db.session.execute(text("CREATE INDEX ix_shell_approvals_user_id ON shell_approvals(user_id)"))
            db.session.commit()
