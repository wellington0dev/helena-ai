"""Purga nonces expirados (fora da janela de replay) — evita crescimento
ilimitado de peer_nonces. Chamado periodicamente pelo scheduler."""
from datetime import timedelta

from flask import current_app

from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.models import PeerNonce


def purge_expired_nonces() -> int:
    window = current_app.config["FEDERATION_REPLAY_WINDOW_SECONDS"]
    cutoff = now_utc() - timedelta(seconds=window + 60)  # margem de segurança
    with write_lock:
        n = (
            db.session.query(PeerNonce)
            .filter(PeerNonce.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        db.session.commit()
    return n
