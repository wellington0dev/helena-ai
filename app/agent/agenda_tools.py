"""Tools de agenda do agente: create/list/update/delete reminder (CLAUDE.md §7)."""
from sqlalchemy import select

from app.agenda import scan
from app.agenda.timeutil import parse_due
from app.extensions import db, write_lock
from app.models import NotificationQueue, Reminder


def _get_owned(user_id: int, reminder_id: int) -> Reminder | None:
    r = db.session.get(Reminder, reminder_id)
    return r if r and r.user_id == user_id else None


def _drop_undelivered(reminder_id: int) -> None:
    """Remove notificações ainda não entregues deste lembrete (fila obsoleta)."""
    db.session.query(NotificationQueue).filter(
        NotificationQueue.reference_id == reminder_id,
        NotificationQueue.type == "reminder",
        NotificationQueue.delivered.is_(False),
    ).delete(synchronize_session=False)


def create_reminder(user_id: int, args: dict) -> dict:
    title = (args.get("title") or "").strip()
    if not title:
        return {"ok": False, "error": "title vazio"}
    kind = args.get("kind") or "simple"
    recurrence = (args.get("recurrence") or "").strip().lower() or None
    if recurrence == "none":
        recurrence = None
    if recurrence:
        if recurrence not in scan.RECURRENCES:
            return {"ok": False, "error": f"recurrence deve ser um de {sorted(scan.RECURRENCES)}"}
        kind = "simple"  # recorrência é sempre estilo simples (1 disparo por ocorrência)
    if kind not in ("agenda", "simple"):
        return {"ok": False, "error": "kind deve ser 'agenda' ou 'simple'"}
    try:
        due_at = parse_due(args["due_at"])
    except (KeyError, ValueError):
        return {"ok": False, "error": "due_at inválido (use ISO 8601)"}

    notify_at = None
    if kind == "simple":
        raw = args.get("notify_at")
        try:
            notify_at = parse_due(raw) if raw else due_at
        except ValueError:
            return {"ok": False, "error": "notify_at inválido"}

    with write_lock:
        r = Reminder(
            user_id=user_id,
            title=title,
            description=(args.get("description") or None),
            due_at=due_at,
            origin=args.get("origin") or "user",
            kind=kind,
            notify_at=notify_at,
            recurrence=recurrence,
        )
        db.session.add(r)
        db.session.commit()
        rid = r.id

    # scan inline: enfileira etapas imediatas sem esperar o cron de 3h
    reminder = db.session.get(Reminder, rid)
    scan.enqueue_for_reminder(reminder)
    return {"ok": True, "reminder_id": rid, "due_at": due_at.isoformat()}


def list_agenda(user_id: int, args: dict) -> dict:
    rows = db.session.scalars(
        select(Reminder)
        .where(Reminder.user_id == user_id)
        .order_by(Reminder.due_at.asc())
    ).all()
    return {"ok": True, "reminders": [r.to_dict() for r in rows]}


def update_reminder(user_id: int, args: dict) -> dict:
    rid = args.get("reminder_id")
    r = _get_owned(user_id, rid) if rid is not None else None
    if r is None:
        return {"ok": False, "error": "lembrete não encontrado"}

    changed = False
    with write_lock:
        if "title" in args and args["title"]:
            r.title = args["title"].strip()
        if "description" in args:
            r.description = args["description"] or None
        if "recurrence" in args:
            rec = (args.get("recurrence") or "").strip().lower() or None
            if rec == "none":
                rec = None
            if rec and rec not in scan.RECURRENCES:
                return {"ok": False, "error": f"recurrence deve ser um de {sorted(scan.RECURRENCES)}"}
            r.recurrence = rec
            if rec:
                r.kind = "simple"  # recorrência é sempre estilo simples
            changed = True
        if args.get("due_at"):
            try:
                r.due_at = parse_due(args["due_at"])
            except ValueError:
                return {"ok": False, "error": "due_at inválido"}
            changed = True

        if changed:
            # mudou data/recorrência: descarta fila obsoleta e reabre as etapas
            _drop_undelivered(r.id)
            r.notified_1w = r.notified_1d = r.notified_6h = False
            r.notified = False
            if r.kind == "simple":
                r.notify_at = r.due_at
        db.session.commit()

    if changed:
        scan.enqueue_for_reminder(db.session.get(Reminder, r.id))
    return {"ok": True, "reminder": db.session.get(Reminder, r.id).to_dict()}


def delete_reminder(user_id: int, args: dict) -> dict:
    rid = args.get("reminder_id")
    r = _get_owned(user_id, rid) if rid is not None else None
    if r is None:
        return {"ok": False, "error": "lembrete não encontrado"}
    with write_lock:
        _drop_undelivered(r.id)  # remove notificações pendentes do lembrete morto
        db.session.delete(r)
        db.session.commit()
    return {"ok": True, "deleted": rid}
