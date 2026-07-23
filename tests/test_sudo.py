"""Portão de sudo: permissão À PARTE de fullcontrol, com aprovação forçada
por padrão. Cobre local (executar_shell) e remoto (executar_ssh) — os dois
passam pelo mesmo `_decide_and_dispatch`."""
from app.agent import shell_tool, ssh_tool
from app.extensions import db
from app.models import ShellApproval, ShellCommand


def _fake_result(exit_code=0, stdout="ok", stderr="", timeout=False):
    return {"exit_code": exit_code, "stdout": stdout, "stderr": stderr, "timeout": timeout}


def test_sudo_bloqueado_sem_sudo_enabled_mesmo_fullcontrol(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True, sudo_enabled=False)
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "sudo whoami"})
        assert r["ok"] is False
        assert "sudo" in r["error"].lower()
        assert db.session.query(ShellCommand).count() == 0


def test_sudo_com_require_approval_pede_aprovacao_mesmo_fullcontrol(app, make_user):
    uid = make_user(
        "f", is_principal=True, shell_full_control=True,
        sudo_enabled=True, sudo_require_approval=True,
    )
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "sudo apt update"})
        assert r.get("pending_approval") is True
        rec = db.session.query(ShellCommand).one()
        assert rec.status == "pending"
        assert rec.command == "sudo apt update"


def test_sudo_permitir_sempre_nao_bypassa_require_approval(app, make_user):
    """O teste mais importante: uma ShellApproval 'permitir sempre' já
    existente pro comando exato NÃO deve bypassar sudo_require_approval."""
    uid = make_user(
        "f", is_principal=True, shell_full_control=True,
        sudo_enabled=True, sudo_require_approval=True,
    )
    with app.app_context():
        db.session.add(ShellApproval(user_id=uid, command="sudo apt update", target_host=""))
        db.session.commit()

        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "sudo apt update"})
        assert r.get("pending_approval") is True
        assert db.session.query(ShellCommand).filter_by(status="pending").count() == 1


def test_sudo_sem_require_approval_roda_direto_fullcontrol(app, make_user, monkeypatch):
    uid = make_user(
        "f", is_principal=True, shell_full_control=True,
        sudo_enabled=True, sudo_require_approval=False,
    )
    calls = []
    monkeypatch.setattr(
        shell_tool, "run_shell",
        lambda command, cwd=None: calls.append(command) or _fake_result(),
    )
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "sudo systemctl restart nginx"})
        assert r.get("executed") is True
        assert calls == ["sudo systemctl restart nginx"]
        assert db.session.query(ShellCommand).count() == 0


def test_comando_sem_sudo_nao_afetado_pelo_refactor(app, make_user, monkeypatch):
    uid = make_user("f", is_principal=True, shell_full_control=True, sudo_enabled=False)
    monkeypatch.setattr(
        shell_tool, "run_shell", lambda command, cwd=None: _fake_result()
    )
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "echo x"})
        assert r.get("executed") is True


def test_sudo_word_boundary_nao_pega_falso_positivo(app, make_user, monkeypatch):
    """'sudo' precisa ser palavra isolada — 'pseudonymize' não deve disparar o gate."""
    uid = make_user("f", is_principal=True, shell_full_control=True, sudo_enabled=False)
    monkeypatch.setattr(
        shell_tool, "run_shell", lambda command, cwd=None: _fake_result()
    )
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = shell_tool.executar_shell(uid, {"comando": "echo pseudonymize"})
        assert r.get("executed") is True


# ------------------------------- via SSH ---------------------------------- #

def test_ssh_sudo_bloqueado_sem_sudo_enabled(app, make_user, monkeypatch):
    uid = make_user("f", is_principal=True, shell_full_control=True, sudo_enabled=False)
    monkeypatch.setattr(shell_tool, "run_remote", lambda host, command: _fake_result())
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "sudo reboot"})
        assert r["ok"] is False
        assert "sudo" in r["error"].lower()
        assert db.session.query(ShellCommand).count() == 0


def test_ssh_sudo_require_approval_pede_aprovacao(app, make_user, monkeypatch):
    uid = make_user(
        "f", is_principal=True, shell_full_control=True,
        sudo_enabled=True, sudo_require_approval=True,
    )
    monkeypatch.setattr(shell_tool, "run_remote", lambda host, command: _fake_result())
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "sudo reboot"})
        assert r.get("pending_approval") is True


def test_ssh_sudo_sem_require_approval_roda_direto(app, make_user, monkeypatch):
    uid = make_user(
        "f", is_principal=True, shell_full_control=True,
        sudo_enabled=True, sudo_require_approval=False,
    )
    calls = []
    monkeypatch.setattr(
        shell_tool, "run_remote",
        lambda host, command: calls.append((host, command)) or _fake_result(),
    )
    with app.app_context():
        shell_tool.reset_shell_budget()
        r = ssh_tool.executar_ssh(uid, {"host": "10.0.0.5", "comando": "sudo reboot"})
        assert r.get("executed") is True
        assert calls == [("10.0.0.5", "sudo reboot")]
