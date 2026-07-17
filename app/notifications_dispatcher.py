"""Poller que toca as notificações da Helena como notificação nativa do SO
onde o servidor roda (desktop) — além da notification_queue já existir para o
app mobile puxar (offline-first).

Mesmo desenho do poller de jobs (app/jobs/worker.py): thread daemon, transação
fresca por ciclo, claim atômico sob write_lock para não disparar duas vezes.
Reusa a MESMA fila (não cria uma nova) — todo tipo de notificação (job_done,
reminder, peer_message, peer_paired) já passa por NotificationQueue, então um
único dispatcher cobre todos os pontos de criação sem precisar mexer neles.

Best-effort: se o SO/ambiente não suporta notificação nativa (VPS headless,
sem notify-send, etc.), a linha é marcada como tentada mesmo assim — não fica
tentando pra sempre e não derruba o poller.
"""
import threading
import time

from app.desktop_notify import DesktopNotifyError, notify
from app.extensions import db, write_lock
from app.models import NotificationQueue
from app.agenda.timeutil import now_utc

_started = False


def _claim_due(limit: int = 20) -> list[int]:
    """Marca como desktop_notified as linhas vencidas ainda não tocadas — claim
    atômico (mesmo padrão do _claim de jobs) para não notificar duas vezes."""
    now = now_utc()
    ids = [
        n.id
        for n in db.session.query(NotificationQueue.id)
        .filter(
            NotificationQueue.desktop_notified.is_(False),
            NotificationQueue.fire_at <= now,
        )
        .order_by(NotificationQueue.fire_at.asc())
        .limit(limit)
        .all()
    ]
    if not ids:
        return []
    with write_lock:
        db.session.query(NotificationQueue).filter(
            NotificationQueue.id.in_(ids), NotificationQueue.desktop_notified.is_(False)
        ).update({NotificationQueue.desktop_notified: True}, synchronize_session=False)
        db.session.commit()
    return ids


def _dispatch_one(app, notification_id: int) -> None:
    with app.app_context():
        n = db.session.get(NotificationQueue, notification_id)
        if n is None:
            return
        title, body = n.title, n.body
        db.session.remove()
    try:
        notify(title, body)
    except DesktopNotifyError as exc:
        app.logger.debug("notificação de desktop não disparou (%s): %s", title, exc)


def start_desktop_notifier(app, poll_interval: float = 5.0):
    """Sobe o poller (daemon) que casa a notification_queue com toasts nativos
    do SO. Idempotente. Desligável via HELENA_DESKTOP_NOTIFICATIONS=0."""
    global _started
    if _started:
        return
    if not app.config.get("DESKTOP_NOTIFICATIONS_ENABLED", True):
        app.logger.info("notificações de desktop desativadas (HELENA_DESKTOP_NOTIFICATIONS=0)")
        return
    _started = True

    def _poller():
        while True:
            time.sleep(poll_interval)
            try:
                with app.app_context():
                    db.session.remove()  # transação fresca: enxerga novos commits
                    ids = _claim_due()
                    db.session.remove()
                for nid in ids:
                    _dispatch_one(app, nid)
            except Exception as exc:  # noqa: BLE001 — poller nunca deve morrer
                app.logger.warning("poller de notificações de desktop: %s", exc)

    threading.Thread(target=_poller, name="desktop-notifier", daemon=True).start()
