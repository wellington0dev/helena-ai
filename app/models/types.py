"""Tipos SQLAlchemy compartilhados."""
from datetime import datetime, timezone

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator):
    """DateTime que sempre lê/escreve em UTC *aware*.

    O SQLite guarda datetime naive e descarta o tzinfo. Sem isto, gravamos
    UTC aware mas lemos de volta naive — o que quebra comparações com
    `datetime.now(timezone.utc)` no scheduler (CLAUDE.md §9).
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            # assume UTC para valores naive que escaparem
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
