"""Dispatcher de notificações nativas do desktop (app/notifications_dispatcher.py):
- só dispara linhas VENCIDAS (fire_at <= agora), nunca antecipa lembretes futuros;
- claim marca desktop_notified pra não notificar a mesma linha duas vezes;
- falha do notify() nativo (SO sem suporte) não derruba o claim nem o poller."""
from datetime import timedelta

from app.agenda.timeutil import now_utc
from app.desktop_notify import DesktopNotifyError
from app.extensions import db
from app.models import NotificationQueue
from app.notifications_dispatcher import _claim_due, _dispatch_one


def _add(app, uid, *, fire_at, desktop_notified=False, title="t", body="b"):
    with app.app_context():
        n = NotificationQueue(
            user_id=uid, title=title, body=body, fire_at=fire_at,
            type="reminder", desktop_notified=desktop_notified,
        )
        db.session.add(n)
        db.session.commit()
        return n.id


def test_claim_due_ignora_notificacao_futura(app, make_user):
    uid = make_user("u")
    _add(app, uid, fire_at=now_utc() + timedelta(hours=1))
    with app.app_context():
        assert _claim_due() == []


def test_claim_due_pega_vencida_e_marca(app, make_user):
    uid = make_user("u")
    nid = _add(app, uid, fire_at=now_utc() - timedelta(seconds=1))
    with app.app_context():
        ids = _claim_due()
        assert ids == [nid]
        n = db.session.get(NotificationQueue, nid)
        assert n.desktop_notified is True


def test_claim_due_nao_repete_ja_notificada(app, make_user):
    uid = make_user("u")
    _add(app, uid, fire_at=now_utc() - timedelta(seconds=1), desktop_notified=True)
    with app.app_context():
        assert _claim_due() == []


def test_dispatch_chama_notify_com_titulo_e_corpo(app, make_user, monkeypatch):
    uid = make_user("u")
    nid = _add(app, uid, fire_at=now_utc(), title="oi", body="tudo bem?")
    calls = []
    monkeypatch.setattr(
        "app.notifications_dispatcher.notify", lambda title, body: calls.append((title, body))
    )
    _dispatch_one(app, nid)
    assert calls == [("oi", "tudo bem?")]


def test_dispatch_engole_erro_do_notify_nativo(app, make_user, monkeypatch):
    """SO sem notify-send (ex.: VPS headless) não deve derrubar o poller."""
    uid = make_user("u")
    nid = _add(app, uid, fire_at=now_utc())

    def _boom(title, body):
        raise DesktopNotifyError("notify-send não instalado")

    monkeypatch.setattr("app.notifications_dispatcher.notify", _boom)
    _dispatch_one(app, nid)  # não deve levantar
