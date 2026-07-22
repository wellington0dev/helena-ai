"""Tool executar_ssh: mesmo gate de permissão do shell local, escopado por
host. `run_remote` é SEMPRE monkeypatchado — nunca um SSH de verdade em teste."""
from app.agent import shell_tool, ssh_tool
from app.extensions import db
from app.models import ShellApproval, ShellCommand


def _fake_result(exit_code=0, stdout="ok", stderr="", timeout=False):
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timeout": timeout}


def test_normal_user_cannot_ssh(app, make_user):
    uid = make_user("normal")
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "uptime"})
        assert r["ok"] is False
        assert "permiss" in r["error"].lower()
        assert db.session.query(ShellCommand).count() == 0


def test_principal_new_ssh_requests_approval(app, make_user):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "uptime"})
        assert r.get("pending_approval") is True
        rec = db.session.query(ShellCommand).one()
        assert rec.status == "pending"
        assert rec.target_host == "10.0.0.5"
        assert rec.command == "uptime"


def test_fullcontrol_runs_ssh_directly(app, make_user, monkeypatch):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    calls = []

    def _fake_run_remote(host, command):
        calls.append((host, command))
        return _fake_result()

    monkeypatch.setattr(shell_tool, "run_remote", _fake_run_remote)
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "uptime"})
        assert r.get("executed") is True
        assert r["exit_code"] == 0
        assert calls == [("10.0.0.5", "uptime")]
        # caminho direto (trusted) nunca cria uma linha ShellCommand
        assert db.session.query(ShellCommand).count() == 0


def test_missing_host_or_command_rejected(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        shell_tool.reset_shell_budget()
        assert ssh_tool.executar_ssh(uid, {"comando": "uptime"})["ok"] is False
        assert ssh_tool.executar_ssh(uid, {"host": "10.0.0.5"})["ok"] is False


def test_budget_shared_with_local_shell(app, make_user, monkeypatch):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    monkeypatch.setattr(shell_tool, "run_remote", lambda host, command: _fake_result())
    with app.app_context():
        shell_tool.reset_shell_budget()
        cap = app.config["MAX_SHELL_PER_TURN"]
        results = [
            ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "echo x"})
            for _ in range(cap + 2)
        ]
        assert any("limite" in (r.get("error") or "") for r in results)


def test_always_allow_scoped_per_host(app, make_user, monkeypatch):
    """Aprovar um comando num host não deve confiar o MESMO comando noutro host."""
    uid = make_user("p", is_principal=True)
    monkeypatch.setattr(shell_tool, "run_remote", lambda host, command: _fake_result())
    with app.app_context():
        db.session.add(ShellApproval(user_id=uid, command="uptime", target_host="hostA"))
        db.session.commit()

        shell_tool.reset_shell_budget()
        # hostA já confiado → roda direto, sem novo pending
        r_a = ssh_tool.executar_ssh(uid, {"host": "hostA", "comando": "uptime"})
        assert r_a.get("executed") is True

        # hostB com o MESMO comando → ainda não confiado, pede aprovação
        r_b = ssh_tool.executar_ssh(uid, {"host": "hostB", "comando": "uptime"})
        assert r_b.get("pending_approval") is True


def test_local_shell_approval_does_not_trust_ssh(app, make_user, monkeypatch):
    """Aprovar 'echo x' localmente não deve confiar 'echo x' via SSH."""
    uid = make_user("p", is_principal=True)
    monkeypatch.setattr(shell_tool, "run_remote", lambda host, command: _fake_result())
    with app.app_context():
        db.session.add(ShellApproval(user_id=uid, command="echo x", target_host=""))
        db.session.commit()

        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "hostA", "comando": "echo x"})
        assert r.get("pending_approval") is True
