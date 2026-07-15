"""Kill switch (panic) + trilha de auditoria."""
from app.agent import shell_tool
from app.extensions import db
from app.models import ShellCommand, User


def test_panic_revokes_and_denies(app, make_user, client, auth):
    uid = make_user("u", is_principal=True, shell_full_control=True)
    with app.app_context():
        db.session.add(ShellCommand(user_id=uid, command="echo", status="pending"))
        db.session.commit()

    r = client.post("/account/panic", headers=auth(uid))
    assert r.status_code == 200
    with app.app_context():
        u = db.session.get(User, uid)
        assert u.is_principal is False and u.shell_full_control is False
        assert db.session.query(ShellCommand).filter_by(user_id=uid, status="pending").count() == 0


def test_audit_records_shell_execution(app, make_user, client, auth):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        shell_tool.executar_shell(uid, {"comando": "echo audit_marker"})

    r = client.get("/account/audit", headers=auth(uid))
    assert r.status_code == 200
    entries = r.get_json()["entries"]
    assert any("audit_marker" in e["detail"] and e["kind"] == "shell" for e in entries)
