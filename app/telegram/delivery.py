"""Entrega de mensagens da Helena para os chats vinculados do Telegram.

Chamado dos MESMOS pontos de fan-out que já alimentam o app/web em tempo real
(emit_new_messages / emit_job_done) e do dispatcher de notificações (lembretes).
Tudo best-effort: uma falha de rede com o Telegram nunca derruba o turno nem o
worker. Só roda se houver token configurado E chats vinculados àquele usuário.
"""
from __future__ import annotations

from flask import current_app

from app.telegram import api, link


def _enabled() -> bool:
    return bool(current_app.config.get("TELEGRAM_BOT_TOKEN"))


def _shell_keyboard(cmd_id) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Permitir", "callback_data": f"shell:{cmd_id}:allow"},
            {"text": "🚫 Negar", "callback_data": f"shell:{cmd_id}:deny"},
            {"text": "♾️ Sempre", "callback_data": f"shell:{cmd_id}:always"},
        ]]
    }


def _send_media(chat_id, user_id: int, msg: dict) -> bool:
    """Tenta enviar a mídia da mensagem. True se enviou; False cai pra texto."""
    from app.media import storage

    path = storage.resolve(user_id, msg["media_url"])
    if path is None:
        return False
    data = path.read_bytes()
    name = path.name
    caption = (msg.get("content") or "").strip() or None
    mtype = msg.get("media_type")
    try:
        if mtype == "image":
            api.send_photo(chat_id, data, name, caption)
        elif mtype == "audio":
            # áudio gerado pela Helena (TTS) e áudios em geral → nota de voz
            api.send_voice(chat_id, data, name, caption)
        else:  # pdf, document, spreadsheet, etc.
            api.send_document(chat_id, data, name, caption)
        return True
    except api.TelegramError as exc:
        current_app.logger.warning("telegram: falha ao enviar mídia: %s", exc)
        return False


def _send_one(chat_id, user_id: int, msg: dict) -> None:
    tool = msg.get("tool_name")
    content = (msg.get("content") or "").strip()
    meta = msg.get("media_meta") or {}

    # pedido de permissão de shell → mensagem com botões
    if tool == "shell_request" and meta.get("status") == "pending":
        cmd = meta.get("command", "")
        motivo = meta.get("motivo") or ""
        text = "🔒 Quero rodar um comando na sua máquina:\n\n"
        text += f"`{cmd}`" if cmd else "(comando)"
        if motivo:
            text += f"\n\nMotivo: {motivo}"
        try:
            api.send_message(chat_id, text, reply_markup=_shell_keyboard(meta.get("cmd_id")))
        except api.TelegramError as exc:
            current_app.logger.warning("telegram: falha no pedido de shell: %s", exc)
        return

    # saída de comando → bloco de terminal
    if tool == "shell_output" and content:
        _safe_send(chat_id, "```\n" + content[:3500] + "\n```")
        return

    # mídia (imagem/áudio/documento gerados) — com fallback pra texto
    if msg.get("media_url"):
        if _send_media(chat_id, user_id, msg):
            return

    if content:
        _safe_send(chat_id, content)


def _safe_send(chat_id, text: str) -> None:
    # Telegram corta em 4096 chars; quebra em pedaços por segurança
    try:
        for i in range(0, len(text), 4000):
            api.send_message(chat_id, text[i:i + 4000])
    except api.TelegramError as exc:
        current_app.logger.warning("telegram: falha ao enviar texto: %s", exc)


def deliver_messages(user_id: int, messages: list[dict]) -> None:
    """Entrega as mensagens do assistant/tool aos chats vinculados do usuário.
    Ignora mensagens do próprio usuário (role=user) — evita eco do que ele
    acabou de mandar."""
    if not _enabled():
        return
    try:
        chats = link.chats_for_user(user_id)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("telegram: falha ao resolver chats: %s", exc)
        return
    if not chats:
        return
    for msg in messages:
        if msg.get("role") == "user":
            continue
        for chat_id in chats:
            try:
                _send_one(chat_id, user_id, msg)
            except Exception as exc:  # noqa: BLE001 — nunca derruba o chamador
                current_app.logger.warning("telegram: erro entregando msg: %s", exc)


def deliver_notification(user_id: int, title: str, body: str) -> None:
    """Entrega uma notificação (ex.: lembrete) aos chats vinculados."""
    if not _enabled():
        return
    try:
        chats = link.chats_for_user(user_id)
    except Exception:  # noqa: BLE001
        return
    text = f"🔔 {title}\n{body}".strip() if body else f"🔔 {title}"
    for chat_id in chats:
        _safe_send(chat_id, text)
