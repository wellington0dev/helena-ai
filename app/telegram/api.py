"""Cliente fino da Bot API do Telegram (via requests, sem lib pesada).

Long-polling (getUpdates) — não precisa de URL pública/webhook. Todas as
funções pegam o token de `current_app.config` e nunca levantam pra fora sem
necessidade: erros viram TelegramError, tratados best-effort pelos chamadores.
"""
from __future__ import annotations

import requests
from flask import current_app

_TIMEOUT = 20  # timeout HTTP padrão (getUpdates usa o seu próprio, maior)


class TelegramError(Exception):
    pass


def _base() -> str:
    token = current_app.config.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise TelegramError("TELEGRAM_BOT_TOKEN não configurado")
    return f"https://api.telegram.org/bot{token}"


def _post(method: str, *, http_timeout: int | None = None, **params) -> dict:
    # `http_timeout` é o timeout da conexão HTTP; params vira o corpo JSON da
    # chamada (onde `timeout` é o long-poll do getUpdates, coisa diferente).
    try:
        r = requests.post(f"{_base()}/{method}", json=params, timeout=http_timeout or _TIMEOUT)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"{method}: {exc}") from exc
    if not data.get("ok"):
        raise TelegramError(f"{method}: {data.get('description', 'erro')}")
    return data.get("result")


def get_me() -> dict:
    return _post("getMe")


def get_updates(offset: int | None, timeout: int) -> list[dict]:
    """Long-poll. `timeout` é o do lado do Telegram; o HTTP espera um pouco mais
    pra não cortar a conexão no meio da espera."""
    params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        params["offset"] = offset
    return _post("getUpdates", http_timeout=timeout + 10, **params)


def send_message(chat_id, text: str, reply_markup: dict | None = None) -> dict:
    params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if reply_markup is not None:
        params["reply_markup"] = reply_markup
    return _post("sendMessage", **params)


def send_chat_action(chat_id, action: str = "typing") -> None:
    try:
        _post("sendChatAction", chat_id=chat_id, action=action)
    except TelegramError:
        pass  # ação de "digitando" é cosmética; nunca atrapalha o fluxo


def answer_callback_query(callback_query_id: str, text: str | None = None) -> None:
    params = {"callback_query_id": callback_query_id}
    if text:
        params["text"] = text
    try:
        _post("answerCallbackQuery", **params)
    except TelegramError:
        pass


def edit_reply_markup(chat_id, message_id: int, reply_markup: dict | None = None) -> None:
    try:
        _post("editMessageReplyMarkup", chat_id=chat_id, message_id=message_id,
              reply_markup=reply_markup or {"inline_keyboard": []})
    except TelegramError:
        pass


def delete_message(chat_id, message_id: int) -> None:
    try:
        _post("deleteMessage", chat_id=chat_id, message_id=message_id)
    except TelegramError:
        pass


# ---- envio de mídia (multipart) ---- #

def _send_file(method: str, field: str, chat_id, data: bytes, filename: str,
               caption: str | None = None) -> dict:
    files = {field: (filename, data)}
    payload = {"chat_id": str(chat_id)}
    if caption:
        payload["caption"] = caption[:1024]
    try:
        r = requests.post(f"{_base()}/{method}", data=payload, files=files, timeout=60)
        body = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise TelegramError(f"{method}: {exc}") from exc
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', 'erro')}")
    return body.get("result")


def send_photo(chat_id, data, filename, caption=None):
    return _send_file("sendPhoto", "photo", chat_id, data, filename, caption)


def send_voice(chat_id, data, filename, caption=None):
    return _send_file("sendVoice", "voice", chat_id, data, filename, caption)


def send_audio(chat_id, data, filename, caption=None):
    return _send_file("sendAudio", "audio", chat_id, data, filename, caption)


def send_document(chat_id, data, filename, caption=None):
    return _send_file("sendDocument", "document", chat_id, data, filename, caption)


# ---- download de mídia recebida ---- #

def get_file_path(file_id: str) -> str:
    result = _post("getFile", file_id=file_id)
    path = result.get("file_path")
    if not path:
        raise TelegramError("getFile: sem file_path")
    return path


def download(file_path: str) -> bytes:
    token = current_app.config.get("TELEGRAM_BOT_TOKEN")
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
    except requests.RequestException as exc:
        raise TelegramError(f"download: {exc}") from exc
    return r.content
