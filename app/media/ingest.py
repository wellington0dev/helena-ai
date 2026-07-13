"""Ingest de mídia de entrada: transcreve áudio e descreve imagem UMA vez.

O resultado (transcript/description/duration) vai para `media_meta`, para que o
`build_history` alimente o modelo com TEXTO em turnos futuros — sem reenviar bytes
nem re-transcrever (CLAUDE.md §3/§7). O arquivo original é sempre preservado.
"""
import wave
from pathlib import Path

from flask import current_app
from google.genai import types

from app.agent.gemini import get_client
from app.media import storage


def _audio_duration_seconds(path: Path) -> float | None:
    """Duração de um WAV (para o player estilo WhatsApp). Só WAV nativamente."""
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            return round(frames / float(rate), 2) if rate else None
    except Exception:  # noqa: BLE001 — outros formatos: duração fica None
        return None


def process(user_id: int, media_type: str, media_url: str, media_meta: dict) -> dict:
    """Enriquece `media_meta` conforme o tipo. Devolve o media_meta atualizado.
    Falhas não são fatais — a mídia entra na conversa mesmo sem transcript."""
    meta = dict(media_meta or {})
    path = storage.resolve(user_id, media_url)
    if path is None:
        return meta

    cfg = current_app.config
    api_key = cfg["GEMINI_API_KEY"]
    model = cfg["GEMINI_MODEL"]
    mime = meta.get("mime") or storage.classify(path.suffix)[1]

    try:
        if media_type == "audio":
            if path.suffix.lower() == ".wav":
                dur = _audio_duration_seconds(path)
                if dur is not None:
                    meta["duration"] = dur
            client = get_client(api_key)
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime),
                    types.Part.from_text(
                        text="Transcreva este áudio em português, apenas o texto falado."
                    ),
                ],
            )
            meta["transcript"] = (resp.text or "").strip()

        elif media_type == "image":
            client = get_client(api_key)
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=path.read_bytes(), mime_type=mime),
                    types.Part.from_text(
                        text="Descreva esta imagem em uma frase curta, em português."
                    ),
                ],
            )
            meta["description"] = (resp.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — ingest é best-effort
        current_app.logger.warning("ingest de mídia falhou: %s", exc)

    return meta
