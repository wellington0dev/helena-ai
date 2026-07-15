"""Recorrência de lembretes: avança sem duplicar, faz catch-up, ajusta calendário."""
from datetime import datetime, timedelta, timezone

import app.agenda.notify_body as notify_body
from app.agenda import scan
from app.agenda.timeutil import now_utc
from app.extensions import db
from app.models import NotificationQueue, Reminder


def _mk(app, uid, offset, recurrence="daily"):
    with app.app_context():
        t = now_utc()
        r = Reminder(user_id=uid, title="t", due_at=t + offset, kind="simple",
                     notify_at=t + offset, recurrence=recurrence)
        db.session.add(r)
        db.session.commit()
        return r.id, t


def test_daily_enqueues_once_then_advances(app, make_user, monkeypatch):
    monkeypatch.setattr(notify_body, "generate_body", lambda r, s, when=None: "x")
    uid = make_user("r")
    rid, t = _mk(app, uid, timedelta(hours=10))
    with app.app_context():
        rec = db.session.get(Reminder, rid)
        assert scan.enqueue_for_reminder(rec, now=t) == 1        # enfileira a ocorrência
        assert scan.enqueue_for_reminder(rec, now=t) == 0        # não duplica
        assert rec.notify_at > t + timedelta(hours=24)           # avançou p/ a próxima
        assert rec.notified is False                             # nunca "conclui"


def test_catchup_does_not_flood_old_occurrences(app, make_user, monkeypatch):
    monkeypatch.setattr(notify_body, "generate_body", lambda r, s, when=None: "x")
    uid = make_user("r2")
    rid, t = _mk(app, uid, timedelta(days=-5))  # 5 dias no passado
    with app.app_context():
        rec = db.session.get(Reminder, rid)
        n = scan.enqueue_for_reminder(rec, now=t)
        assert n <= 2                    # não enfileira 5 atrasadas
        assert rec.notify_at > t         # próxima é futura


def test_next_occurrence_calendar():
    assert scan.next_occurrence(datetime(2026, 1, 31, 8, tzinfo=timezone.utc), "monthly").day == 28
    assert scan.next_occurrence(datetime(2028, 2, 29, 8, tzinfo=timezone.utc), "yearly").year == 2029
    assert scan.next_occurrence(datetime(2026, 7, 13, 8, tzinfo=timezone.utc), "weekly").day == 20
