"""Nonces já vistos de cada peer — proteção contra replay de requests HMAC.

Só precisa sobreviver à janela de replay (FEDERATION_REPLAY_WINDOW_SECONDS);
purgado periodicamente por server/app/federation/cleanup.py.
"""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PeerNonce(db.Model):
    __tablename__ = "peer_nonces"
    __table_args__ = (db.UniqueConstraint("peer_id", "nonce", name="uq_peer_nonce"),)

    id = db.Column(db.Integer, primary_key=True)
    peer_id = db.Column(
        db.Integer, db.ForeignKey("peers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nonce = db.Column(db.Text, nullable=False)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False, index=True)
