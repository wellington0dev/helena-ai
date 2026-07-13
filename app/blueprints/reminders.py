"""REST de agenda: CRUD de lembretes + fila de notificações (CLAUDE.md §12)."""
from datetime import timedelta

from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import select

from app.agent import agenda_tools
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.models import NotificationQueue, Reminder

reminders_bp = Blueprint("reminders", __name__)


def _uid() -> int:
    return int(get_jwt_identity())


# --------------------------------------------------------------------------- #
# Reminders CRUD (reusa a lógica das tools, já validada e escopada)
# --------------------------------------------------------------------------- #

@reminders_bp.get("/reminders")
@jwt_required()
def list_reminders():
    return jsonify(agenda_tools.list_agenda(_uid(), {})), 200


@reminders_bp.post("/reminders")
@jwt_required()
def create_reminder():
    res = agenda_tools.create_reminder(_uid(), request.get_json(silent=True) or {})
    return jsonify(res), (201 if res.get("ok") else 400)


@reminders_bp.put("/reminders/<int:reminder_id>")
@jwt_required()
def update_reminder(reminder_id: int):
    data = {**(request.get_json(silent=True) or {}), "reminder_id": reminder_id}
    res = agenda_tools.update_reminder(_uid(), data)
    return jsonify(res), (200 if res.get("ok") else 404)


@reminders_bp.delete("/reminders/<int:reminder_id>")
@jwt_required()
def delete_reminder(reminder_id: int):
    res = agenda_tools.delete_reminder(_uid(), {"reminder_id": reminder_id})
    return jsonify(res), (200 if res.get("ok") else 404)


# --------------------------------------------------------------------------- #
# Notificações (para o WorkManager do app)
# --------------------------------------------------------------------------- #

@reminders_bp.get("/notifications/pending")
@jwt_required()
def pending():
    """Notificações com fire_at nas próximas 24h ainda não entregues."""
    uid = _uid()
    horizon = now_utc() + timedelta(hours=24)
    rows = db.session.scalars(
        select(NotificationQueue)
        .where(
            NotificationQueue.user_id == uid,
            NotificationQueue.delivered.is_(False),
            NotificationQueue.fire_at <= horizon,
        )
        .order_by(NotificationQueue.fire_at.asc())
    ).all()
    return jsonify(notifications=[n.to_dict() for n in rows]), 200


@reminders_bp.post("/notifications/ack")
@jwt_required()
def ack():
    """Marca notificações como entregues (materializadas no dispositivo)."""
    uid = _uid()
    ids = (request.get_json(silent=True) or {}).get("ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify(error="ids (lista) obrigatório"), 400
    with write_lock:
        updated = (
            db.session.query(NotificationQueue)
            .filter(
                NotificationQueue.user_id == uid,
                NotificationQueue.id.in_(ids),
            )
            .update({NotificationQueue.delivered: True}, synchronize_session=False)
        )
        db.session.commit()
    return jsonify(ok=True, acked=updated), 200
