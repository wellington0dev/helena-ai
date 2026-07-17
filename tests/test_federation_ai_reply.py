"""Federação Fase 2: diálogo IA-IA com teto de turnos.

São o portão de correção real desta fase (a qualidade do texto do Gemini não
é verificável automaticamente) — cobrem o gate de enqueue no webhook, o
incremento/reset do streak, e a conclusão especializada do job
(`_complete_federation_reply`) que NÃO deve seguir o caminho genérico de job
chat-facing. Sem chamada de rede real: `send_message`/`get_client` são
monkeypatched.
"""
import time

import pytest

from app.extensions import db
from app.federation import crypto
from app.federation.client import FederationError
from app.jobs import worker
from app.models import Job, Message, NotificationQueue, Peer, PeerMessage


def _make_peer(app, uid, *, secret="s3gredo-de-teste", link_id="link-abc", label="Amigo",
                trust_level="a_averiguar", ai_dialogue_enabled=False, ai_turn_streak=0):
    with app.app_context():
        p = Peer(
            user_id=uid, link_id=link_id, shared_secret=secret,
            remote_base_url="https://peer.example.ts.net", label=label,
            trust_level=trust_level, ai_dialogue_enabled=ai_dialogue_enabled,
            ai_turn_streak=ai_turn_streak,
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


def _post_webhook(client, body=b'{"body":"oi, tudo bem?"}', secret="s3gredo-de-teste"):
    headers = _signed_headers(secret, "POST", "/federation/webhook/message", body)
    return client.post("/federation/webhook/message", data=body, headers=headers,
                        content_type="application/json")


def _pending_ai_jobs(app):
    with app.app_context():
        return db.session.query(Job).filter_by(type="federation_reply", status="pending").count()


class _FakeModels:
    def __init__(self, text="Oi! Por aqui tudo certo, e por aí?"):
        self.text = text
        self.calls = 0

    def generate_content(self, **_kwargs):
        self.calls += 1

        class _Resp:
            text = self.text

        return _Resp()


class _FakeClient:
    def __init__(self, text="Oi! Por aqui tudo certo, e por aí?"):
        self.models = _FakeModels(text)


# --------------------------------------------------------------------------- #
# Gate de enqueue no webhook
# --------------------------------------------------------------------------- #

def test_webhook_enqueues_ai_reply_when_enabled_and_trusted(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    r = _post_webhook(client)
    assert r.status_code == 200
    with app.app_context():
        job = db.session.query(Job).filter_by(type="federation_reply").first()
        assert job is not None
        assert job.status == "pending"
        assert job.user_id == uid
        assert job.payload["peer_id"] is not None


def test_webhook_does_not_enqueue_when_toggle_disabled(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=False)
    r = _post_webhook(client)
    assert r.status_code == 200
    assert _pending_ai_jobs(app) == 0


def test_webhook_does_not_enqueue_when_not_confiavel(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid, trust_level="nao_confiavel", ai_dialogue_enabled=True)
    r = _post_webhook(client)
    assert r.status_code == 200
    assert _pending_ai_jobs(app) == 0


def test_webhook_does_not_enqueue_when_owner_paused(app, make_user, client, auth):
    uid = make_user("owner")
    _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    client.post("/account/panic", headers=auth(uid))
    # panic também bloqueia o próprio recebimento (403) — usamos um segundo peer
    # só pra provar que, mesmo se o recebimento passasse, o gate de IA olha o
    # pause. Como o webhook já barra em 403 antes de chegar no gate de IA,
    # a asserção relevante aqui é simplesmente: nenhum job foi criado.
    r = _post_webhook(client)
    assert r.status_code == 403
    assert _pending_ai_jobs(app) == 0


def test_webhook_does_not_enqueue_when_streak_at_cap(app, make_user, client):
    uid = make_user("owner")
    cap = None
    with app.app_context():
        from flask import current_app
        cap = current_app.config["FEDERATION_MAX_AI_TURNS"]
    _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True, ai_turn_streak=cap)
    r = _post_webhook(client)
    assert r.status_code == 200
    assert _pending_ai_jobs(app) == 0


# --------------------------------------------------------------------------- #
# Reset de streak pelo humano
# --------------------------------------------------------------------------- #

def test_send_peer_message_resets_streak_on_success(app, make_user, client, auth, monkeypatch):
    uid = make_user("a")
    pid = _make_peer(app, uid, ai_turn_streak=2)
    monkeypatch.setattr("app.blueprints.federation.send_message", lambda peer, body, **_kw: None)
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 201
    with app.app_context():
        assert db.session.get(Peer, pid).ai_turn_streak == 0


def test_send_peer_message_resets_streak_even_on_delivery_failure(app, make_user, client, auth, monkeypatch):
    uid = make_user("a")
    pid = _make_peer(app, uid, ai_turn_streak=2)

    def _boom(peer, body, **_kw):
        raise FederationError("timeout")

    monkeypatch.setattr("app.blueprints.federation.send_message", _boom)
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 502
    with app.app_context():
        assert db.session.get(Peer, pid).ai_turn_streak == 0


# --------------------------------------------------------------------------- #
# _complete_federation_reply — conclusão especializada do job
# --------------------------------------------------------------------------- #

def _make_job(app, uid, pid, message_id=1):
    with app.app_context():
        job = Job(user_id=uid, type="federation_reply",
                   payload={"peer_id": pid, "message_id": message_id}, status="pending")
        db.session.add(job)
        db.session.commit()
        return job.id


def test_complete_federation_reply_creates_ai_message_not_chat_message(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="incoming",
                                    body="oi, tudo bem?", status="received"))
        db.session.commit()
    job_id = _make_job(app, uid, pid)

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)
    monkeypatch.setattr("app.jobs.worker.send_message", lambda peer, body, **_kw: None)

    job_done_calls = []
    peer_msg_calls = []
    monkeypatch.setattr("app.jobs.worker.emit_job_done", lambda uid, msg: job_done_calls.append((uid, msg)))
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda uid, msg: peer_msg_calls.append((uid, msg)))

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        job = db.session.get(Job, job_id)
        assert job.status == "done"

        assert db.session.query(Message).count() == 0
        assert db.session.query(NotificationQueue).filter_by(type="job_done").count() == 0

        ai_msg = db.session.query(PeerMessage).filter_by(
            peer_id=pid, direction="outgoing", authored_by="ai"
        ).first()
        assert ai_msg is not None
        assert ai_msg.status == "sent"

        peer = db.session.get(Peer, pid)
        assert peer.ai_turn_streak == 1

    assert job_done_calls == []
    assert len(peer_msg_calls) == 1
    assert fake_client.models.calls == 1


def test_complete_federation_reply_increments_streak_even_if_delivery_fails(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="incoming",
                                    body="oi", status="received"))
        db.session.commit()
    job_id = _make_job(app, uid, pid)

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)

    def _boom(peer, body, **_kw):
        raise FederationError("timeout")

    monkeypatch.setattr("app.jobs.worker.send_message", _boom)
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda uid, msg: None)

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        peer = db.session.get(Peer, pid)
        assert peer.ai_turn_streak == 1  # sobe mesmo com falha de entrega — teto é sobre GERAÇÃO nossa
        job = db.session.get(Job, job_id)
        assert job.status == "error"
        ai_msg = db.session.query(PeerMessage).filter_by(peer_id=pid, direction="outgoing").first()
        assert ai_msg.status == "failed"


@pytest.mark.parametrize("mutate", [
    lambda peer: setattr(peer, "ai_dialogue_enabled", False),
    lambda peer: setattr(peer, "trust_level", "nao_confiavel"),
    lambda peer: setattr(peer, "ai_turn_streak", 999),
])
def test_complete_federation_reply_rechecks_conditions_at_execution_time(app, make_user, monkeypatch, mutate):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="incoming",
                                    body="oi", status="received"))
        db.session.commit()
    job_id = _make_job(app, uid, pid)

    # simula o estado mudando ENTRE o enqueue (webhook) e a execução (worker)
    with app.app_context():
        peer = db.session.get(Peer, pid)
        mutate(peer)
        db.session.commit()

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)
    monkeypatch.setattr("app.jobs.worker.send_message", lambda peer, body, **_kw: None)
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda uid, msg: None)

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        job = db.session.get(Job, job_id)
        assert job.status == "done"  # não se aplica mais != erro
        assert db.session.query(PeerMessage).filter_by(direction="outgoing", peer_id=pid).count() == 0
    assert fake_client.models.calls == 0  # Gemini nunca foi chamado


def test_complete_federation_reply_rechecks_owner_paused_at_execution_time(app, make_user, client, auth, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="incoming",
                                    body="oi", status="received"))
        db.session.commit()
    job_id = _make_job(app, uid, pid)

    client.post("/account/panic", headers=auth(uid))  # muda depois do enqueue

    fake_client = _FakeClient()
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: fake_client)
    monkeypatch.setattr("app.jobs.worker.send_message", lambda peer, body, **_kw: None)
    monkeypatch.setattr("app.jobs.worker.emit_peer_message", lambda uid, msg: None)

    worker._complete_federation_reply(app, job_id)

    with app.app_context():
        job = db.session.get(Job, job_id)
        assert job.status == "done"
    assert fake_client.models.calls == 0


def test_run_job_dispatches_federation_reply_to_specialized_branch(app, make_user, monkeypatch):
    uid = make_user("owner")
    pid = _make_peer(app, uid, trust_level="confiavel", ai_dialogue_enabled=True)
    with app.app_context():
        db.session.add(PeerMessage(peer_id=pid, user_id=uid, direction="incoming",
                                    body="oi", status="received"))
        db.session.commit()
    job_id = _make_job(app, uid, pid)

    calls = []
    monkeypatch.setattr("app.jobs.worker._complete_federation_reply", lambda app, jid: calls.append(jid))
    worker._run_job(app, job_id)
    assert calls == [job_id]
