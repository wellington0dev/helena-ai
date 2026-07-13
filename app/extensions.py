"""Extensões compartilhadas e configuração de concorrência do SQLite."""
import threading

from flask_jwt_extended import JWTManager
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
jwt = JWTManager()
# threading mode: evita eventlet/gevent (ver CLAUDE.md §15)
socketio = SocketIO(async_mode="threading", cors_allowed_origins="*")

# Serializa escritas em background (worker de jobs, scheduler).
# Leituras seguem concorrentes; writers pegam este lock.
write_lock = threading.Lock()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """Ativa WAL + busy_timeout em cada conexão SQLite (CLAUDE.md §2)."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()
