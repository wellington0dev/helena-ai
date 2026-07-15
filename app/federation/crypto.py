"""Assinatura HMAC entre instâncias pareadas + geração de código/segredo.

Toda request de federação (exceto o resgate de pareamento, que usa o próprio
código como credencial de uso único) é assinada com o `shared_secret` do par:

    canonical = f"{method}\\n{path}\\n{timestamp}\\n{nonce}\\n{sha256(body).hexdigest()}"
    signature = HMAC-SHA256(shared_secret, canonical)

`path` (não a URL completa) deixa a assinatura independente de host/scheme —
o remetente resolve a URL do peer do jeito que quiser, só o path importa pra
verificação. O corpo é assinado pelo hash, não incluído inteiro na string
canônica, pra manter o tamanho da assinatura previsível.
"""
import hashlib
import hmac
import secrets
import time
import uuid
from urllib.parse import urlparse

# alfabeto sem 0/O/1/I/L — evita ambiguidade quando o código é lido em voz alta
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 10


def generate_pairing_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def generate_link_id() -> str:
    return uuid.uuid4().hex


def generate_shared_secret() -> str:
    return secrets.token_hex(32)


def generate_nonce() -> str:
    return secrets.token_hex(16)


def _canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    body_hash = hashlib.sha256(body or b"").hexdigest()
    return f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes) -> str:
    canonical = _canonical(method, path, timestamp, nonce, body)
    return hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def verify(
    secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes, signature: str
) -> bool:
    expected = sign(secret, method, path, timestamp, nonce, body)
    return hmac.compare_digest(expected, signature or "")


def timestamp_fresh(timestamp: str, window_seconds: int) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    return abs(time.time() - ts) <= window_seconds


def validate_peer_url(url: str) -> str | None:
    """Devolve None se a URL é aceitável (https://, ou http://localhost pra
    testes locais); senão devolve uma mensagem de erro."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return "URL inválida"
    if parsed.scheme == "https" and parsed.netloc:
        return None
    if parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"):
        return None
    return "URL precisa ser https:// (http:// só é aceito pra localhost/127.0.0.1, em testes)"
