"""Wipe da conta ('só esvaziar') tem que limpar TODAS as tabelas do usuário —
inclusive as sensíveis novas (shell_commands guarda stdout, shell_approvals,
saved_commands, routines). Regressão de privacidade que já mordeu antes."""
from datetime import datetime, timezone

from app.extensions import db
from app.models import (
    AiNote, AuditEntry, Message, Reminder, Routine, SavedCommand, ShellApproval,
    ShellCommand,
)

_TABLES = [Message, Reminder, ShellCommand, ShellApproval, SavedCommand, Routine,
           AiNote, AuditEntry]


def test_wipe_clears_all_user_tables(app, make_user, client, auth):
    uid = make_user("w")
    with app.app_context():
        db.session.add_all([
            Message(user_id=uid, role="user", content="oi"),
            Reminder(user_id=uid, title="x", due_at=datetime(2030, 1, 1, tzinfo=timezone.utc)),
            ShellCommand(user_id=uid, command="echo segredo", status="done", stdout="segredo"),
            ShellApproval(user_id=uid, command="echo"),
            SavedCommand(user_id=uid, name="c", command="echo"),
            Routine(user_id=uid, name="r", steps=[]),
            AiNote(user_id=uid, content="nota"),
            AuditEntry(user_id=uid, kind="shell", detail="echo x", exit_code=0),
        ])
        db.session.commit()

    resp = client.post("/account/wipe", json={"deleteAccount": False}, headers=auth(uid))
    assert resp.status_code == 200

    with app.app_context():
        for model in _TABLES:
            assert db.session.query(model).filter_by(user_id=uid).count() == 0, model.__name__
