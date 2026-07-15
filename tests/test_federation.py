"""Federação Fase 1: pareamento, assinatura HMAC, escopo de dono, panic.

Sem chamada de rede real: rotas públicas são exercitadas diretamente via
`client.post()` (simulando o que outra instância mandaria); rotas JWT que
DISPARAM saída (`POST /federation/peers`, envio de mensagem) têm o cliente
HTTP de saída (`app.federation.client`) trocado por monkeypatch.
"""
import hashlib
import hmac
import time

import pytest

from app.extensions import db, write_lock
from app.federation import crypto
from app.models import NotificationQueue, PairingCode, Peer, PeerMessage, PeerNonce, User


def _make_peer(app, uid, secret="s3gredo-de-teste", link_id="link-abc", label="Amigo"):
    with app.app_context():
        p = Peer(user_id=uid, link_id=link_id, shared_secret=secret,
                  remote_base_url="https://peer.example.ts.net", label=label)
        db.session.add(p)
        db.session.commit()
        return p.id


def _signed_headers(secret, method, path, body: bytes, *, timestamp=None, nonce=None):
    timestamp = timestamp or str(int(time.time()))
    nonce = nonce or "nonce-" + hashlib.sha1(body).hexdigest()[:8]
    sig = crypto.sign(secret, method, path, timestamp, nonce, body)
    return {
        "X-Helena-Link-Id": "link-abc",
        "X-Helena-Timestamp": timestamp,
        "X-Helena-Nonce": nonce,
        "X-Helena-Signature": sig,
    }


# --------------------------------------------------------------------------- #
# Crypto
# --------------------------------------------------------------------------- #

def test_crypto_roundtrip_and_tamper_detection():
    secret = "segredo"
    body = b'{"body":"oi"}'
    sig = crypto.sign(secret, "POST", "/federation/webhook/message", "1000", "n1", body)
    assert crypto.verify(secret, "POST", "/federation/webhook/message", "1000", "n1", body, sig)
    # corrompe 1 byte do corpo → assinatura não bate mais
    assert not crypto.verify(secret, "POST", "/federation/webhook/message", "1000", "n1", body + b"x", sig)
    # timestamp diferente também quebra
    assert not crypto.verify(secret, "POST", "/federation/webhook/message", "1001", "n1", body, sig)


def test_validate_peer_url():
    assert crypto.validate_peer_url("https://x.ts.net") is None
    assert crypto.validate_peer_url("http://localhost:5001") is None
    assert crypto.validate_peer_url("http://evil.example.com") is not None


# --------------------------------------------------------------------------- #
# Pareamento
# --------------------------------------------------------------------------- #

def test_pairing_generate_and_redeem_success(app, make_user, client, auth):
    uid = make_user("a")
    r = client.post("/federation/peers/pairing-codes", headers=auth(uid))
    assert r.status_code == 201
    code = r.get_json()["code"]

    # simula o POST que a outra instância faria ao resgatar o código
    r2 = client.post("/federation/pairing/redeem", json={
        "code": code, "peer_base_url": "https://peer.example.ts.net", "label": "Peer B",
    })
    assert r2.status_code == 201
    data = r2.get_json()
    assert data["link_id"] and data["shared_secret"]

    with app.app_context():
        peer = db.session.query(Peer).filter_by(user_id=uid).first()
        assert peer is not None and peer.label == "Peer B" and peer.trust_level == "a_averiguar"
        assert db.session.query(NotificationQueue).filter_by(
            user_id=uid, type="peer_paired"
        ).count() == 1
        assert db.session.get(PairingCode, db.session.query(PairingCode).first().id).used is True


def test_pairing_redeem_expired(app, make_user, client, auth):
    uid = make_user("a")
    with app.app_context():
        from datetime import timedelta

        from app.agenda.timeutil import now_utc

        db.session.add(PairingCode(
            user_id=uid, code_hash=crypto.hash_code("ABCDEFGHJK"),
            expires_at=now_utc() - timedelta(seconds=1),
        ))
        db.session.commit()
    r = client.post("/federation/pairing/redeem", json={
        "code": "ABCDEFGHJK", "peer_base_url": "https://peer.example.ts.net",
    })
    assert r.status_code == 404


def test_pairing_redeem_reuse_rejected(app, make_user, client, auth):
    uid = make_user("a")
    code = client.post("/federation/peers/pairing-codes", headers=auth(uid)).get_json()["code"]
    payload = {"code": code, "peer_base_url": "https://peer.example.ts.net"}
    r1 = client.post("/federation/pairing/redeem", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/federation/pairing/redeem", json=payload)
    assert r2.status_code == 404


def test_pairing_redeem_while_paused(app, make_user, client, auth):
    uid = make_user("a")
    code = client.post("/federation/peers/pairing-codes", headers=auth(uid)).get_json()["code"]
    client.post("/account/panic", headers=auth(uid))
    r = client.post("/federation/pairing/redeem", json={
        "code": code, "peer_base_url": "https://peer.example.ts.net",
    })
    assert r.status_code == 403


def test_generate_code_blocked_when_paused(app, make_user, client, auth):
    uid = make_user("a")
    client.post("/account/panic", headers=auth(uid))
    r = client.post("/federation/peers/pairing-codes", headers=auth(uid))
    assert r.status_code == 403


def test_redeem_peer_code_requires_public_url(app, make_user, client, auth):
    uid = make_user("b")  # FEDERATION_PUBLIC_URL não configurada no ambiente de teste
    r = client.post("/federation/peers", json={
        "code": "XXXXXXXXXX", "base_url": "https://a.example.ts.net",
    }, headers=auth(uid))
    assert r.status_code == 400


def test_redeem_peer_code_calls_client_and_creates_peer(app, make_user, client, auth, monkeypatch):
    uid = make_user("b")
    app.config["FEDERATION_PUBLIC_URL"] = "https://b.example.ts.net"
    monkeypatch.setattr(
        "app.blueprints.federation.redeem_pairing_code",
        lambda base_url, code, my_url, my_label: {
            "link_id": "abc123", "shared_secret": "shh", "label": "Peer A",
        },
    )
    r = client.post("/federation/peers", json={
        "code": "XXXXXXXXXX", "base_url": "https://a.example.ts.net",
    }, headers=auth(uid))
    assert r.status_code == 201
    with app.app_context():
        peer = db.session.query(Peer).filter_by(user_id=uid).first()
        assert peer.link_id == "abc123" and peer.label == "Peer A"


# --------------------------------------------------------------------------- #
# Webhook (mensagem recebida)
# --------------------------------------------------------------------------- #

def test_webhook_accepts_valid_signature(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)
    body = b'{"body":"oi, tudo bem?"}'
    headers = _signed_headers("s3gredo-de-teste", "POST", "/federation/webhook/message", body)
    r = client.post("/federation/webhook/message", data=body, headers=headers,
                     content_type="application/json")
    assert r.status_code == 200
    with app.app_context():
        assert db.session.query(PeerMessage).filter_by(user_id=uid, direction="incoming").count() == 1
        assert db.session.query(NotificationQueue).filter_by(user_id=uid, type="peer_message").count() == 1


def test_webhook_rejects_wrong_secret(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)
    body = b'{"body":"oi"}'
    headers = _signed_headers("SEGREDO-ERRADO", "POST", "/federation/webhook/message", body)
    r = client.post("/federation/webhook/message", data=body, headers=headers)
    assert r.status_code == 401


def test_webhook_rejects_tampered_body(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)
    body = b'{"body":"oi"}'
    headers = _signed_headers("s3gredo-de-teste", "POST", "/federation/webhook/message", body)
    r = client.post("/federation/webhook/message", data=body + b"extra", headers=headers)
    assert r.status_code == 401


def test_webhook_rejects_stale_timestamp(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)
    body = b'{"body":"oi"}'
    old_ts = str(int(time.time()) - 10_000)
    headers = _signed_headers("s3gredo-de-teste", "POST", "/federation/webhook/message", body, timestamp=old_ts)
    r = client.post("/federation/webhook/message", data=body, headers=headers)
    assert r.status_code == 401


def test_webhook_rejects_replay(app, make_user, client):
    uid = make_user("owner")
    _make_peer(app, uid)
    body = b'{"body":"oi"}'
    headers = _signed_headers("s3gredo-de-teste", "POST", "/federation/webhook/message", body, nonce="fixo")
    r1 = client.post("/federation/webhook/message", data=body, headers=headers,
                      content_type="application/json")
    assert r1.status_code == 200
    r2 = client.post("/federation/webhook/message", data=body, headers=headers,
                      content_type="application/json")
    assert r2.status_code == 401
    with app.app_context():
        assert db.session.query(PeerMessage).filter_by(user_id=uid).count() == 1  # não duplicou


def test_webhook_rejects_when_owner_paused(app, make_user, client, auth):
    uid = make_user("owner")
    _make_peer(app, uid)
    client.post("/account/panic", headers=auth(uid))
    body = b'{"body":"oi"}'
    headers = _signed_headers("s3gredo-de-teste", "POST", "/federation/webhook/message", body)
    r = client.post("/federation/webhook/message", data=body, headers=headers)
    assert r.status_code == 403


# --------------------------------------------------------------------------- #
# CRUD / escopo de dono
# --------------------------------------------------------------------------- #

def test_peer_ownership_scoping(app, make_user, client, auth):
    uid_a = make_user("a")
    uid_b = make_user("b")
    pid = _make_peer(app, uid_a)
    for method, path in (("get", f"/federation/peers/{pid}/messages"),
                          ("put", f"/federation/peers/{pid}"),
                          ("delete", f"/federation/peers/{pid}")):
        r = getattr(client, method)(path, headers=auth(uid_b), json={"label": "x"})
        assert r.status_code == 404


def test_trust_level_validation(app, make_user, client, auth):
    uid = make_user("a")
    pid = _make_peer(app, uid)
    r = client.put(f"/federation/peers/{pid}", json={"trust_level": "muito_confiavel"}, headers=auth(uid))
    assert r.status_code == 400
    r2 = client.put(f"/federation/peers/{pid}", json={"trust_level": "confiavel"}, headers=auth(uid))
    assert r2.status_code == 200
    assert r2.get_json()["peer"]["trust_level"] == "confiavel"


# --------------------------------------------------------------------------- #
# Panic corta a federação (envio e recebimento)
# --------------------------------------------------------------------------- #

def test_panic_severs_outgoing_message(app, make_user, client, auth, monkeypatch):
    uid = make_user("a")
    pid = _make_peer(app, uid)
    client.post("/account/panic", headers=auth(uid))
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 403


def test_resume_reenables_federation(app, make_user, client, auth):
    uid = make_user("a")
    client.post("/account/panic", headers=auth(uid))
    assert client.post("/federation/peers/pairing-codes", headers=auth(uid)).status_code == 403
    r = client.post("/federation/resume", headers=auth(uid))
    assert r.status_code == 200
    assert client.post("/federation/peers/pairing-codes", headers=auth(uid)).status_code == 201


def test_send_message_success_updates_status(app, make_user, client, auth, monkeypatch):
    uid = make_user("a")
    pid = _make_peer(app, uid)
    monkeypatch.setattr("app.blueprints.federation.send_message", lambda peer, body, **_kw: None)
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 201
    assert r.get_json()["message"]["status"] == "sent"


def test_send_message_failure_marks_failed(app, make_user, client, auth, monkeypatch):
    from app.federation.client import FederationError

    uid = make_user("a")
    pid = _make_peer(app, uid)

    def _boom(peer, body, **_kw):
        raise FederationError("timeout")

    monkeypatch.setattr("app.blueprints.federation.send_message", _boom)
    r = client.post(f"/federation/peers/{pid}/messages", json={"body": "oi"}, headers=auth(uid))
    assert r.status_code == 502
    assert r.get_json()["message"]["status"] == "failed"
