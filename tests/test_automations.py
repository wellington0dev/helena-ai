"""Procedência: comando salvo pelo USUÁRIO roda silencioso; pela IA pede card."""
from app.agent import automations_tools as at
from app.agent.shell_tool import reset_shell_budget
from app.extensions import db
from app.models import Routine, SavedCommand, ShellCommand


def _cmd(app, uid, created_by, name="teste", command="echo OK"):
    with app.app_context():
        db.session.add(SavedCommand(user_id=uid, name=name, command=command, created_by=created_by))
        db.session.commit()


def test_user_authored_runs_silent(app, make_user):
    uid = make_user("p", is_principal=True)
    _cmd(app, uid, "user")
    with app.app_context():
        reset_shell_budget()
        r = at.executar_comando(uid, {"nome": "teste"})
        assert r.get("executed") is True  # pré-aprovado
        assert db.session.query(ShellCommand).filter_by(status="pending").count() == 0


def test_ai_authored_requires_card(app, make_user):
    uid = make_user("p", is_principal=True)
    _cmd(app, uid, "ai")
    with app.app_context():
        reset_shell_budget()
        r = at.executar_comando(uid, {"nome": "teste"})
        assert r.get("pending_approval") is True  # ainda pede aprovação
        assert db.session.query(ShellCommand).filter_by(status="pending").count() == 1


def test_fullcontrol_runs_ai_silent(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    _cmd(app, uid, "ai")
    with app.app_context():
        reset_shell_budget()
        r = at.executar_comando(uid, {"nome": "teste"})
        assert r.get("executed") is True


def test_normal_user_denied(app, make_user):
    uid = make_user("n")
    _cmd(app, uid, "user")
    with app.app_context():
        r = at.executar_comando(uid, {"nome": "teste"})
        assert r["ok"] is False


def test_name_lookup_ignores_case_and_accent(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    _cmd(app, uid, "user", name="Modo Trabalho")
    with app.app_context():
        reset_shell_budget()
        r = at.executar_comando(uid, {"nome": "modo trabalho"})
        assert r.get("executed") is True


def test_routine_skips_missing_command_ref(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        db.session.add(Routine(user_id=uid, name="rot", created_by="user", steps=[
            {"kind": "shell", "value": "echo A"},
            {"kind": "command", "value": "nao_existe"},  # ref quebrada → pula
        ]))
        db.session.commit()
        reset_shell_budget()
        r = at.executar_lista(uid, {"nome": "rot"})
        assert r.get("executed") is True
        assert "puladas" in (r.get("info") or "")
