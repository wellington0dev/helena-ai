"""Endpoint de decisão de shell: dono só, claim atômico (anti-replay/double-exec)."""
import app.blueprints.commands as commands_bp
from app.extensions import db
from app.models import ShellCommand


def _pending(app, uid):
    with app.app_context():
        rec = ShellCommand(user_id=uid, command="echo OK", status="pending")
        db.session.add(rec)
        db.session.commit()
        return rec.id


def test_decision_ownership_and_replay(app, make_user, client, auth, monkeypatch):
    # não chama o Gemini no re-invoke
    monkeypatch.setattr(commands_bp.runner, "handle_user_turn", lambda *a, **k: [])
    owner = make_user("owner", is_principal=True)
    other = make_user("other", is_principal=True)
    cid = _pending(app, owner)

    # outro usuário não pode decidir
    r = client.post(f"/commands/{cid}/decision", json={"decision": "allow"}, headers=auth(other))
    assert r.status_code == 404

    # dono aprova → executa uma vez
    r = client.post(f"/commands/{cid}/decision", json={"decision": "allow"}, headers=auth(owner))
    assert r.status_code == 200

    # replay do mesmo comando → 409 (não executa de novo)
    r = client.post(f"/commands/{cid}/decision", json={"decision": "allow"}, headers=auth(owner))
    assert r.status_code == 409


def test_deny_does_not_execute(app, make_user, client, auth, monkeypatch):
    monkeypatch.setattr(commands_bp.runner, "handle_user_turn", lambda *a, **k: [])
    uid = make_user("u", is_principal=True)
    cid = _pending(app, uid)
    r = client.post(f"/commands/{cid}/decision", json={"decision": "deny"}, headers=auth(uid))
    assert r.status_code == 200
    with app.app_context():
        assert db.session.get(ShellCommand, cid).status == "denied"
