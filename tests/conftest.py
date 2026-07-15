"""Fixtures de teste: app isolado (SQLite temporário), client e helpers.

O env é ajustado ANTES de importar o app — a Config lê os.environ no import.
Os testes focam nos caminhos CRÍTICOS de segurança e não chamam o Gemini.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="helena-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["HELENA_DATA_DIR"] = _TMP
os.environ["HELENA_MEDIA_DIR"] = f"{_TMP}/media"
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ["JWT_SECRET_KEY"] = "test-secret"

import pytest  # noqa: E402
from flask_jwt_extended import create_access_token  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402


@pytest.fixture()
def app():
    application = create_app()
    with application.app_context():
        db.drop_all()
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def make_user(app):
    """Cria um usuário e devolve o id. Aceita flags (is_principal, shell_full_control)."""
    def _make(username="user", **flags):
        with app.app_context():
            u = User(username=username)
            u.set_password("pw")
            for k, v in flags.items():
                setattr(u, k, v)
            db.session.add(u)
            db.session.commit()
            return u.id
    return _make


@pytest.fixture()
def auth(app):
    """Devolve headers Authorization para um user id."""
    def _auth(uid):
        with app.app_context():
            return {"Authorization": f"Bearer {create_access_token(identity=str(uid))}"}
    return _auth
