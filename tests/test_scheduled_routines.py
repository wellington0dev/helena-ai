"""Rotinas agendadas rodam SOZINHAS — o gate vai no disparo, contra o estado atual:
panic/rebaixamento param; ai-authored nunca roda; coalesce não inunda atrasados."""
from datetime import timedelta

from app.agenda import routine_scheduler
from app.agenda.timeutil import now_utc
from app.agent import automations_tools as at
from app.extensions import db
from app.models import Message, Routine, User


def _routine(app, uid, created_by="user", offset=timedelta(minutes=-1), recurrence=None):
    with app.app_context():
        r = Routine(user_id=uid, name="rot", created_by=created_by, enabled=True,
                    next_run=now_utc() + offset, recurrence=recurrence,
                    steps=[{"kind": "shell", "value": "echo scheduled"}])
        db.session.add(r)
        db.session.commit()
        return r.id


def _outputs(app, uid):
    with app.app_context():
        return db.session.query(Message).filter_by(user_id=uid, tool_name="shell_output").count()


def test_runs_for_principal_owner(app, make_user):
    uid = make_user("p", is_principal=True)
    _routine(app, uid)
    with app.app_context():
        assert routine_scheduler.run_due_routines() == 1
    assert _outputs(app, uid) == 1


def test_panic_stops_scheduled_run(app, make_user):
    uid = make_user("p", is_principal=True)
    _routine(app, uid)
    with app.app_context():
        db.session.get(User, uid).is_principal = False  # panic/rebaixamento
        db.session.commit()
        assert routine_scheduler.run_due_routines() == 0  # NÃO roda
    assert _outputs(app, uid) == 0


def test_ai_authored_never_runs_scheduled(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    _routine(app, uid, created_by="ai")
    with app.app_context():
        assert routine_scheduler.run_due_routines() == 0
    assert _outputs(app, uid) == 0


def test_coalesce_runs_once_and_advances(app, make_user):
    uid = make_user("p", is_principal=True)
    rid = _routine(app, uid, offset=timedelta(days=-3), recurrence="daily")
    with app.app_context():
        assert routine_scheduler.run_due_routines() == 1  # UMA vez, não 3
        r = db.session.get(Routine, rid)
        assert r.next_run > now_utc()  # avançou pro futuro
        assert r.enabled is True


def test_agendar_lista_refuses_ai_authored(app, make_user):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        db.session.add(Routine(user_id=uid, name="airot", created_by="ai",
                               steps=[{"kind": "shell", "value": "echo x"}]))
        db.session.commit()
        r = at.agendar_lista(uid, {"nome": "airot", "quando": "2030-01-01T08:00:00"})
        assert r["ok"] is False  # IA não agenda lista ai-authored


def test_agendar_lista_refuses_normal_user(app, make_user):
    uid = make_user("n")  # sem permissão
    with app.app_context():
        db.session.add(Routine(user_id=uid, name="r", created_by="user",
                               steps=[{"kind": "shell", "value": "echo x"}]))
        db.session.commit()
        r = at.agendar_lista(uid, {"nome": "r", "quando": "2030-01-01T08:00:00"})
        assert r["ok"] is False
