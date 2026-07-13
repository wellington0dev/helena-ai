"""Utilidades de tempo para a agenda."""
from datetime import datetime, timezone


def local_tz():
    """Fuso local do servidor (o app roda num PC/VPS com fuso fixo)."""
    return datetime.now().astimezone().tzinfo


def parse_due(value: str) -> datetime:
    """Parseia um `due_at` ISO 8601 do modelo. Naive é interpretado como
    horário LOCAL do servidor e convertido para UTC aware (CLAUDE.md §9)."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
