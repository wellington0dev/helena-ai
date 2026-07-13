"""Storage de mídia no filesystem: /data/media/<user_id>/<uuid>.<ext>.

`media_url` guardado no banco é o caminho relativo `<user_id>/<arquivo>`.
A resolução para caminho absoluto sempre passa por `safe_join`, impedindo
path traversal (`..`) e vazamento entre usuários.
"""
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.utils import safe_join

# extensão → (media_type, mime)
_EXT_MAP = {
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "webp": ("image", "image/webp"),
    "gif": ("image", "image/gif"),
    "wav": ("audio", "audio/wav"),
    "mp3": ("audio", "audio/mpeg"),
    "ogg": ("audio", "audio/ogg"),
    "m4a": ("audio", "audio/mp4"),
    "aac": ("audio", "audio/aac"),  # gravação nativa (capacitor-voice-recorder)
    "webm": ("audio", "audio/webm"),  # gravação no navegador (MediaRecorder)
    "pdf": ("pdf", "application/pdf"),
    "docx": ("document", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": ("spreadsheet", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "txt": ("document", "text/plain"),
}


def classify(ext: str) -> tuple[str, str]:
    """Devolve (media_type, mime) para uma extensão; genérico se desconhecida."""
    return _EXT_MAP.get(ext.lower().lstrip("."), ("document", "application/octet-stream"))


def _user_dir(user_id: int) -> Path:
    base = Path(current_app.config["MEDIA_DIR"]) / str(user_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_bytes(user_id: int, data: bytes, ext: str) -> str:
    """Salva bytes e devolve o `media_url` relativo `<user_id>/<uuid>.<ext>`."""
    ext = ext.lower().lstrip(".")
    fname = f"{uuid.uuid4().hex}.{ext}"
    (_user_dir(user_id) / fname).write_bytes(data)
    return f"{user_id}/{fname}"


def resolve(user_id: int, media_url: str) -> Path | None:
    """Resolve `media_url` para caminho absoluto DENTRO da pasta do usuário.

    Retorna None se o caminho escapar (traversal) ou o arquivo não existir.
    O `user_id` autenticado ancora a resolução — não confiamos no path do request.
    """
    # media_url pode vir como "<user_id>/<file>"; usamos só o nome do arquivo
    # e ancoramos no diretório do usuário AUTENTICADO.
    fname = Path(media_url).name
    joined = safe_join(str(Path(current_app.config["MEDIA_DIR"]) / str(user_id)), fname)
    if joined is None:
        return None
    p = Path(joined)
    return p if p.is_file() else None


def owner_of(media_url: str) -> int | None:
    """Extrai o user_id dono a partir do prefixo do media_url, se houver."""
    parts = Path(media_url).parts
    if len(parts) >= 2 and parts[0].isdigit():
        return int(parts[0])
    return None
