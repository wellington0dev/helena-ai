"""Chamadas HTTP de SAÍDA pra outra instância da Helena (assinadas, Fase 1).

Nunca é chamado dentro de `write_lock` (server/app/blueprints/federation.py
libera o lock antes de chamar isto) — uma chamada de rede lenta dentro do
lock serializaria todo escritor do banco atrás dela.
"""
import json
import time

import requests
from flask import current_app

from app.federation import crypto


class FederationError(Exception):
    """Falha ao falar com outra instância (rede, timeout, resposta não-2xx)."""


def _timeout():
    secs = current_app.config["FEDERATION_HTTP_TIMEOUT_SECONDS"]
    return (5, secs)  # (connect, read)


def _post_signed(base_url: str, path: str, link_id: str, secret: str, payload: dict) -> dict:
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    nonce = crypto.generate_nonce()
    signature = crypto.sign(secret, "POST", path, timestamp, nonce, body)
    headers = {
        "Content-Type": "application/json",
        "X-Helena-Link-Id": link_id,
        "X-Helena-Timestamp": timestamp,
        "X-Helena-Nonce": nonce,
        "X-Helena-Signature": signature,
    }
    try:
        r = requests.post(base_url.rstrip("/") + path, data=body, headers=headers, timeout=_timeout())
    except requests.RequestException as exc:
        raise FederationError(f"não foi possível conectar a {base_url}: {exc}") from exc
    if r.status_code >= 300:
        raise FederationError(f"{base_url}{path} devolveu {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise FederationError(f"resposta inválida de {base_url}{path}") from exc


def redeem_pairing_code(base_url: str, code: str, my_public_url: str, my_label: str) -> dict:
    """Resgata um código de pareamento gerado por outra instância. Devolve
    {link_id, shared_secret, label} do peer, ou levanta FederationError.

    Não é assinado (ainda não existe segredo compartilhado nesse momento) — a
    credencial é o próprio código, de uso único e curta duração."""
    try:
        r = requests.post(
            base_url.rstrip("/") + "/federation/pairing/redeem",
            json={"code": code, "peer_base_url": my_public_url, "label": my_label},
            timeout=_timeout(),
        )
    except requests.RequestException as exc:
        raise FederationError(f"não foi possível conectar a {base_url}: {exc}") from exc
    if r.status_code >= 300:
        raise FederationError(f"pareamento recusado ({r.status_code}): {r.text[:200]}")
    try:
        return r.json()
    except ValueError as exc:
        raise FederationError("resposta de pareamento inválida") from exc


def send_message(
    peer, body_text: str, *, kind: str = "chat",
    request_id: str | None = None, in_reply_to: str | None = None,
) -> None:
    """Manda uma mensagem assinada pro peer. Levanta FederationError se falhar.
    kind/request_id/in_reply_to (Fase 3) são omitidos do payload quando no
    valor default, pra manter o payload de chat comum idêntico ao de antes."""
    payload = {"body": body_text}
    if kind != "chat":
        payload["kind"] = kind
    if request_id is not None:
        payload["request_id"] = request_id
    if in_reply_to is not None:
        payload["in_reply_to"] = in_reply_to
    _post_signed(
        peer.remote_base_url,
        "/federation/webhook/message",
        peer.link_id,
        peer.shared_secret,
        payload,
    )
