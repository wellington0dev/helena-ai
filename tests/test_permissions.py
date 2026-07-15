"""Gate de permissão do shell: normal recusa, principal pede card, fullcontrol roda."""
from app.agent import shell_tool
from app.extensions import db
from app.models import ShellCommand


def test_normal_user_cannot_run_shell(app, make_user):
    uid = make_user("normal")
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "echo hi"})
        assert r["ok"] is False
        assert "permiss" in r["error"].lower()
        assert db.session.query(ShellCommand).count() == 0  # nada foi criado/rodado


def test_principal_new_command_requests_approval(app, make_user):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "echo hi"})
        assert r.get("pending_approval") is True
        rec = db.session.query(ShellCommand).one()
        assert rec.status == "pending"


def test_fullcontrol_runs_directly(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "echo hi"})
        assert r.get("executed") is True
        assert r["exit_code"] == 0
        assert db.session.query(ShellCommand).filter_by(status="pending").count() == 0


def test_shell_budget_caps_per_turn(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        cap = app.config["MAX_SHELL_PER_TURN"]
        results = [shell_tool.executar_shell(uid, {"comando": "echo x"}) for _ in range(cap + 2)]
        assert any("limite" in (r.get("error") or "") for r in results)
