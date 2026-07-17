"""Federação Fase 3: compartilhar resultado, pedir ajuda estruturado, e
iniciativa da IA (tools do agente principal de chat).

São o portão de correção real desta fase — cobrem os gates de
`federation_share_result`/`federation_ask_peer` (ai_can_initiate, trust_level
=="confiavel", kill-switch, cooldown independente do ai_turn_streak da Fase
2), a validação de correlação help_request/help_response no webhook (a parte
mais importante: nunca confiar num `in_reply_to` que o peer alegou sem
verificar que referencia algo que NÓS enviamos), e o tagging kind-aware do
pipeline isolado de resposta automática — sem chamar Gemini de verdade.
"""
import time

import pytest

from app.agent import federation_tools
from app.extensions import db
from app.federation import crypto
from app.federation.client import FederationError
from app.jobs import worker
from app.models import Job, NotificationQueue, Peer, PeerMessage


def _make_peer(app, uid, *, secret="s3gredo-de-teste", link_id="link-abc", label="Amigo",
                trust_level="a_averiguar", ai_dialogue_enabled=False, ai_turn_streak=0,
                ai_can_initiate=False, ai_initiate_last_at=None):
    with app.app_context():
        p = Peer(
            user_id=uid, link_id=link_id, shared_secret=secret,
            remote_base_url="https://peer.example.ts.net", label=label,
            trust_level=trust_level, ai_dialogue_enabled=ai_dialogue_enabled,
            ai_turn_streak=ai_turn_streak, ai_can_initiate=ai_can_initiate,
            ai_initiate_last_at=ai_initiate_last_at,
        )
        db.session.add(p)
        db.session.commit()
        return p.id


def _signed_headers(secret, method, path, body: bytes, *, timestamp=None, nonce=None):
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or "nonce-" + str(time.monotonic_ns())
    sig = crypto.sign(secret, method, path, timestamp, nonce, body)
    return {
        "X-Helena-Link-Id": "link-abc",
        "X-Helena-Timestamp": timestamp,
        "X-Helena-Nonce": nonce,
        "X-Helena-Signature": sig,
    }


def _post_webhook(client, body: bytes, secret="s3gredo-de-teste"):
    headers = _signed_headers(secret, "POST", "/federation/webhook/message", body)
    return client.post("/federation/webhook/message", data=body, headers=headers,
                        content_type="application/json")


# --------------------------------------------------------------------------- #
# Gates de federation_share_result / federation_ask_peer
# --------------------------------------------------------------------------- #

def test_share_result_blocked_when_ai_can_initiate_false(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=False)
    calls = []
    monkeypatch.setattr("app.agent.federation_tools.send_message", lambda *a, **k: calls.append(1))
    with app.app_context():
        result = federation_tools.federation_share_result(uid, {"peer_id": pid, "summary": "oi"})
    assert result["ok"] is False
    assert calls == []
    with app.app_context():
        assert db.session.query(PeerMessage).filter_by(peer_id=pid).count() == 0


@pytest.mark.parametrize("trust_level", ["a_averiguar", "nao_confiavel"])
def test_ask_peer_blocked_when_not_confiavel(app, make_user, trust_level):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level=trust_level, ai_can_initiate=True)
    with app.app_context():
        result = federation_tools.federation_ask_peer(uid, {"peer_id": pid, "question": "oi"})
    assert result["ok"] is False
    assert "confi" in result["error"].lower()


def test_share_result_blocked_when_paused(app, make_user, client, auth):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True)
    client.post("/account/panic", headers=auth(uid))
    with app.app_context():
        result = federation_tools.federation_share_result(uid, {"peer_id": pid, "summary": "oi"})
    assert result["ok"] is False
    assert "pânico" in result["error"] or "pausada" in result["error"]


def test_ask_peer_blocked_by_cooldown(app, make_user):
    from app.agenda.timeutil import now_utc

    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True,
                      ai_initiate_last_at=now_utc())
    with app.app_context():
        result = federation_tools.federation_ask_peer(uid, {"peer_id": pid, "question": "oi"})
    assert result["ok"] is False
    assert "espera" in result["error"] or "pouco" in result["error"]


def test_ask_peer_allowed_when_cooldown_expired(app, make_user, monkeypatch):
    from datetime import timedelta

    from app.agenda.timeutil import now_utc

    uid = make_user("owner")
    old = now_utc() - timedelta(hours=2)
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True,
                      ai_initiate_last_at=old)
    monkeypatch.setattr("app.agent.federation_tools.send_message", lambda *a, **k: None)
    monkeypatch.setattr("app.agent.federation_tools.emit_peer_message", lambda *a, **k: None)
    with app.app_context():
        result = federation_tools.federation_ask_peer(uid, {"peer_id": pid, "question": "oi"})
    assert result["ok"] is True


# --------------------------------------------------------------------------- #
# Sucesso / falha de envio — efeitos colaterais
# --------------------------------------------------------------------------- #

def test_share_result_success_creates_message_and_notifies(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True)
    monkeypatch.setattr("app.agent.federation_tools.send_message", lambda *a, **k: None)
    emitted = []
    monkeypatch.setattr("app.agent.federation_tools.emit_peer_message", lambda uid, m: emitted.append(m))

    with app.app_context():
        result = federation_tools.federation_share_result(uid, {"peer_id": pid, "summary": "descobri X"})
        assert result["ok"] is True

        msg = db.session.query(PeerMessage).filter_by(peer_id=pid).first()
        assert msg.direction == "outgoing" and msg.authored_by == "ai"
        assert msg.kind == "task_share" and msg.request_id is None
        assert msg.status == "sent"

        peer = db.session.get(Peer, pid)
        assert peer.ai_initiate_last_at is not None

        assert db.session.query(NotificationQueue).filter_by(user_id=uid).count() == 1
    assert len(emitted) == 1


def test_ask_peer_success_generates_request_id(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True)
    monkeypatch.setattr("app.agent.federation_tools.send_message", lambda *a, **k: None)
    monkeypatch.setattr("app.agent.federation_tools.emit_peer_message", lambda *a, **k: None)

    with app.app_context():
        result = federation_tools.federation_ask_peer(uid, {"peer_id": pid, "question": "vc sabe X?"})
        assert result["ok"] is True
        assert result["request_id"]

        msg = db.session.query(PeerMessage).filter_by(peer_id=pid).first()
        assert msg.kind == "help_request"
        assert msg.request_id == result["request_id"]


def test_initiate_does_not_touch_ai_turn_streak(app, make_user, monkeypatch):
    """O contador da Fase 2 (resposta automática) e o cooldown da Fase 3
    (iniciativa) são desacoplados por construção — este é o teste que prova."""
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True, ai_turn_streak=2)
    monkeypatch.setattr("app.agent.federation_tools.send_message", lambda *a, **k: None)
    monkeypatch.setattr("app.agent.federation_tools.emit_peer_message", lambda *a, **k: None)

    with app.app_context():
        federation_tools.federation_share_result(uid, {"peer_id": pid, "summary": "x"})
        assert db.session.get(Peer, pid).ai_turn_streak == 2


def test_initiate_failure_marks_failed_no_notification_but_still_emits(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_can_initiate=True)

    def _boom(*a, **k):
        raise FederationError("timeout")

    monkeypatch.setattr("app.agent.federation_tools.send_message", _boom)
    emitted = []
    monkeypatch.setattr("app.agent.federation_tools.emit_peer_message", lambda uid, m: emitted.append(m))

    with app.app_context():
        result = federation_tools.federation_share_result(uid, {"peer_id": pid, "summary": "x"})
        assert result["ok"] is False

        msg = db.session.query(PeerMessage).filter_by(peer_id=pid).first()
        assert msg.status == "failed"
        assert db.session.query(NotificationQueue).filter_by(user_id=uid).count() == 0
    assert len(emitted) == 1  # visibilidade mesmo em falha — nunca silencioso


# --------------------------------------------------------------------------- #
# Correlação no webhook — o guardrail mais importante da fase
# --------------------------------------------------------------------------- #

def test_webhook_help_response_verified_when_in_reply_to_matches_our_request(app, make_user, client):
    uid = make_user("owner")
    pid = _make_peer(app, uid)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="outgoing",
                                    body="pergunta", status="sent",
                                    kind="help_request", request_id="req-123"))
        db.session.commit()

    import json
    body = json.dumps({"body": "aqui está", "kind": "help_response", "in_reply_to": "req-123"}).encode()
    r = _post_webhook(client, body)
    assert r.status_code == 200
    with app.app_context():
        msg = (db.session.query(PeerMessage)
               .filter_by(peer_id=pid, direction="incoming", kind="help_response").first())
        assert msg is not None
        assert msg.verified_request_message_id is not None


def test_webhook_help_response_unverified_when_in_reply_to_unknown(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)

    import json
    body = json.dumps({"body": "aqui está", "kind": "help_response", "in_reply_to": "nao-existe"}).encode()
    r = _post_webhook(client, body)
    assert r.status_code == 200
    with app.app_context():
        msg = (db.session.query(PeerMessage)
               .filter_by(direction="incoming", kind="help_response").first())
        assert msg is not None
        assert msg.verified_request_message_id is None
        assert msg.in_reply_to == "nao-existe"  # guardado cru, só não "verificado"


def test_webhook_help_response_forgery_across_peers_not_verified(app, make_user, client):
    """peer B tenta reivindicar o request_id de um pedido que mandamos pro peer A."""
    uid = make_user("owner")
    pid_a = _make_peer(app, uid, link_id="link-a", secret="secret-a", label="Peer A")
    _make_peer(app, uid, link_id="link-abc", secret="s3gredo-de-teste", label="Peer B")

    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid_a, user_id=uid, direction="outgoing",
                                    body="pergunta pro A", status="sent",
                                    kind="help_request", request_id="req-do-a"))
        db.session.commit()

    import json
    body = json.dumps({"body": "forjado", "kind": "help_response", "in_reply_to": "req-do-a"}).encode()
    r = _post_webhook(client, body)  # assinado com o secret do peer B (link-abc)
    assert r.status_code == 200
    with app.app_context():
        msg = (db.session.query(PeerMessage)
               .filter_by(direction="incoming", kind="help_response").first())
        assert msg.verified_request_message_id is None


def test_webhook_unknown_kind_falls_back_to_chat(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)

    import json
    body = json.dumps({"body": "oi", "kind": "algo_novo_do_futuro"}).encode()
    r = _post_webhook(client, body)
    assert r.status_code == 200
    with app.app_context():
        msg = db.session.query(PeerMessage).filter_by(direction="incoming").first()
        assert msg.kind == "chat"


# --------------------------------------------------------------------------- #
# _complete_federation_reply — kind-aware, isolamento intocado
# --------------------------------------------------------------------------- #

class _FakeModels:
    def __init__(self, text="resposta automática"):
        self.text = text
        self.calls = 0

    def generate_content(self, **_kwargs):
        self.calls += 1

        class _Resp:
            text = self.text

        return _Resp()


class _FakeClient:
    def __init__(self, text="resposta automática"):
        self.models = _FakeModels(text)


def _make_job(app, uid, pid, message_id):
    with app.app_context():
        job = Job(user_id=uid, type="federation_reply",
                   payload={"peer_id": pid, "message_id": message_id}, status="pending")
        db.session.add(job)
        db.session.commit()
        return job.id


def test_complete_federation_reply_tags_help_response_when_triggered_by_help_request(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        trigger = PeerMessage(peer_id=pid, user_id=uid, direction="incoming", body="me ajuda?",
                               status="received", kind="help_request", request_id="req-xyz")
        db.session.add(trigger)
        db.session.commit()
        trigger_id = trigger.id
    job_id = _make_job(app, uid, pid, trigger_id)

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)
    sent_kwargs = {}

    def _capture_send(peer, body, **kwargs):
        sent_kwargs.update(kwargs)

    monkeypatch.setattr("app.jobs.worker.send_message", _capture_send)
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda *a, **k: None)

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        out = (db.session.query(PeerMessage)
               .filter_by(peer_id=pid, direction="outgoing", authored_by="ai").first())
        assert out.kind == "help_response"
        assert out.in_reply_to == "req-xyz"
    assert sent_kwargs.get("kind") == "help_response"
    assert sent_kwargs.get("in_reply_to") == "req-xyz"


def test_complete_federation_reply_stays_chat_when_triggered_by_chat(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        trigger = PeerMessage(peer_id=pid, user_id=uid, direction="incoming", body="oi",
                               status="received", kind="chat")
        db.session.add(trigger)
        db.session.commit()
        trigger_id = trigger.id
    job_id = _make_job(app, uid, pid, trigger_id)

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)
    sent_kwargs = {}

    def _capture_send(peer, body, **kwargs):
        sent_kwargs.update(kwargs)

    monkeypatch.setattr("app.jobs.worker.send_message", _capture_send)
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda *a, **k: None)

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        out = (db.session.query(PeerMessage)
               .filter_by(peer_id=pid, direction="outgoing", authored_by="ai").first())
        assert out.kind == "chat"
        assert out.in_reply_to is None
    assert sent_kwargs.get("kind") == "chat"


# --------------------------------------------------------------------------- #
# send_peer_message (humano) com reply_to_message_id
# --------------------------------------------------------------------------- #

def test_send_peer_message_reply_to_help_request_tags_help_response(app, make_user, client, auth, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid)
    with app.app_context():
        origin = PeerMessage(peer_id=pid, user_id=uid, direction="incoming", body="me ajuda?",
                              status="received", kind="help_request", request_id="req-1")
        db.session.add(origin)
        db.session.commit()
        origin_id = origin.id

    monkeypatch.setattr("app.blueprints.federation.send_message", lambda *a, **k: None)
    r = client.post(f"/federation/peers/{pid}/messages",
                     json={"body": "aqui vai", "reply_to_message_id": origin_id}, headers=auth(uid))
    assert r.status_code == 201
    data = r.get_json()["message"]
    assert data["kind"] == "help_response"
    assert data["in_reply_to"] == "req-1"


def test_send_peer_message_reply_to_wrong_peer_rejected(app, make_user, client, auth):
    uid = make_user("owner")
    pid_a = _make_peer(app, uid, link_id="link-a", secret="secret-a", label="A")
    pid_b = _make_peer(app, uid, link_id="link-b", secret="secret-b", label="B")
    with app.app_context():
        origin = PeerMessage(peer_id=pid_a, user_id=uid, direction="incoming", body="me ajuda?",
                              status="received", kind="help_request", request_id="req-1")
        db.session.add(origin)
        db.session.commit()
        origin_id = origin.id

    r = client.post(f"/federation/peers/{pid_b}/messages",
                     json={"body": "aqui vai", "reply_to_message_id": origin_id}, headers=auth(uid))
    assert r.status_code == 400


def test_send_peer_message_without_reply_id_stays_chat(app, make_user, client, auth, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid)
    monkeypatch.setattr("app.blueprints.federation.send_message", lambda *a, **k: None)
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 201
    assert r.get_json()["message"]["kind"] == "chat"


# --------------------------------------------------------------------------- #
# Exposição das tools em build_tool_declarations
# --------------------------------------------------------------------------- #

def test_federation_tools_absent_without_any_peer(app, make_user):
    from app.agent.tools import build_tool_declarations

    uid = make_user("owner")
    with app.app_context():
        decls = build_tool_declarations(uid)
        names = {d.name for d in decls.function_declarations}
    assert "federation_share_result" not in names
    assert "federation_ask_peer" not in names
    assert "list_federation_peers" not in names


def test_federation_tools_present_with_a_peer_regardless_of_trust(app, make_user):
    from app.agent.tools import build_tool_declarations

    uid = make_user("owner")
    _make_peer(app, uid, trust_level="a_averiguar", ai_can_initiate=False)
    with app.app_context():
        decls = build_tool_declarations(uid)
        names = {d.name for d in decls.function_declarations}
    assert "federation_share_result" in names
    assert "federation_ask_peer" in names
    assert "list_federation_peers" in names


def test_federation_tools_never_reach_restricted_background_loops():
    """As tools de iniciativa só existem no loop normal de chat
    (build_tool_declarations) — nunca dentro de research/desktop_task, que
    usam seus PRÓPRIOS tool_declarations restritos. Pin contra regressão
    futura: se alguém um dia importar FEDERATION_INITIATE_DECLS pra dentro
    de LOOP_TOOLS/DESKTOP_TASK_TOOLS, este teste quebra."""
    from app.agent.desktop_task_tools import DESKTOP_TASK_TOOLS
    from app.agent.loop_tools import LOOP_TOOLS

    federation_names = {"list_federation_peers", "federation_share_result", "federation_ask_peer"}
    loop_names = {d.name for d in LOOP_TOOLS.function_declarations}
    desktop_names = {d.name for d in DESKTOP_TASK_TOOLS.function_declarations}
    assert not (federation_names & loop_names)
    assert not (federation_names & desktop_names)
