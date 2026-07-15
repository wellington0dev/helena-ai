"""Registro de auditoria: uma linha por ação executada na máquina.

Best-effort — nunca deixa a ação falhar por causa da auditoria.
"""
from flask import current_app

from app.extensions import db, write_lock
from app.models import AuditEntry


def record(user_id: int, kind: str, detail: str, exit_code: int | None = None) -> None:
    try:
        with write_lock:
            db.session.add(
                AuditEntry(user_id=user_id, kind=kind, detail=detail[:2000], exit_code=exit_code)
            )
            db.session.commit()
    except Exception as exc:  # noqa: BLE001 — auditoria não pode derrubar a ação
        current_app.logger.warning("auditoria falhou: %s", exc)
        db.session.rollback()
