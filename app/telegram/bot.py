"""Bot do Telegram: long-polling + dispatch das mensagens/botões.

Espelha o cliente do `helena chat`: login por email+senha e, a partir daí, o
MESMO agente com as MESMAS permissões da conta (chat, mídia, jobs, aprovação de
shell, lembretes). Desenho igual aos outros pollers do projeto (worker,
notifier): thread daemon, app_context por tarefa, nunca morre por exceção.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from app.extensions import db, write_lock
from app.telegram import api, link

_HELP = (
    "Oi! Eu sou a Helena 💙\n\n"
    "Pra começar, faça login na sua conta:\n"
    "• /login — entrar com email e senha\n"
    "• /logout — desvincular este chat\n"
    "• /whoami — ver a conta vinculada\n"
    "• /historico — ver as últimas mensagens da conversa\n"
    "• /cancel — cancelar o login em andamento\n\n"
    "Depois de logar, é só conversar normalmente — texto, áudio, foto e "
    "documentos funcionam, igual no app."
)


# --------------------------------------------------------------------------- #
# Extração de mídia de uma mensagem do Telegram
# --------------------------------------------------------------------------- #

def _extract_media(message: dict):
    """Devolve (file_id, ext, filename) da mídia da mensagem, ou None."""
    if "photo" in message:
        return message["photo"][-1]["file_id"], "jpg", "foto.jpg"
    if "voice" in message:
        return message["voice"]["file_id"], "ogg", "audio.ogg"
    if "audio" in message:
        a = message["audio"]
        name = a.get("file_name") or "audio.mp3"
        ext = name.rsplit(".", 1)[-1] if "." in name else "mp3"
        return a["file_id"], ext, name
    if "document" in message:
        d = message["document"]
        name = d.get("file_name") or "arquivo"
        ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
        return d["file_id"], ext, name
    return None


def _ingest_media(user_id: int, message: dict):
    """Baixa a mídia do Telegram e persiste como mídia da Helena.
    Devolve (media_url, media_type, media_meta) ou None."""
    from app.media import ingest, storage

    found = _extract_media(message)
    if not found:
        return None
    file_id, ext, filename = found
    try:
        data = api.download(api.get_file_path(file_id))
    except api.TelegramError:
        return None
    media_url = storage.save_bytes(user_id, data, ext)
    media_type, mime = storage.classify(ext)
    meta = {"mime": mime, "original_name": filename, "size": len(data)}
    meta = ingest.process(user_id, media_type, media_url, meta)
    return media_url, media_type, meta


# --------------------------------------------------------------------------- #
# Processamento de um turno (mensagem do usuário → agente → respostas)
# --------------------------------------------------------------------------- #

def _send_history(app, chat_id, user_id: int, limit: int = 15, header: str = "🕓 Últimas mensagens:") -> None:
    """Espelha as últimas mensagens da conversa (o histórico é único por conta,
    compartilhado entre app/web/CLI/Telegram) — dá continuidade ao trocar de
    cliente."""
    from app.models import Message

    with app.app_context():
        rows = (
            db.session.query(Message)
            .filter(Message.user_id == user_id)
            .order_by(Message.id.desc())
            .limit(limit)
            .all()
        )
        rows.reverse()
        lines = []
        for m in rows:
            if m.role == "user":
                who = "Você"
            elif m.role == "assistant":
                who = "Helena"
            else:
                who = "⚙️"
            c = (m.content or "").strip()
            if not c and m.media_url:
                c = f"[{m.media_type or 'mídia'}]"
            if c:
                lines.append(f"*{who}:* {c}")
        if not lines:
            api.send_message(chat_id, "(ainda não temos histórico por aqui)")
            return
        text = header + "\n\n" + "\n\n".join(lines)
        for i in range(0, len(text), 4000):
            try:
                api.send_message(chat_id, text[i:i + 4000])
            except api.TelegramError:
                break


def _process_turn(app, chat_id, user_id: int, text: str, message: dict) -> None:
    from app.agent import runner
    from app.models import Message
    from app.realtime import emit_new_messages

    with app.app_context():
        try:
            api.send_chat_action(chat_id, "typing")
            media = _ingest_media(user_id, message)
            media_url = media_type = None
            media_meta = None
            if media:
                media_url, media_type, media_meta = media

            content = (text or "").strip()
            if not content and not media_url:
                return
            with write_lock:
                user_msg = Message(
                    user_id=user_id, role="user", content=content,
                    media_url=media_url, media_type=media_type, media_meta=media_meta,
                )
                db.session.add(user_msg)
                db.session.commit()
                user_dict = user_msg.to_dict()
                since_id = user_msg.id

            replies = runner.handle_user_turn(user_id, since_id)
            reply_dicts = [m.to_dict() for m in replies]
            # mesmo fan-out do HTTP: entrega ao Telegram (aqui) + web/app (socket)
            emit_new_messages(user_id, [user_dict, *reply_dicts])
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("telegram: turno falhou (chat=%s): %s", chat_id, exc)
            try:
                api.send_message(chat_id, "opa, me embananei aqui 😅 tenta de novo?")
            except api.TelegramError:
                pass
        finally:
            db.session.remove()


# --------------------------------------------------------------------------- #
# Login (email+senha) e comandos
# --------------------------------------------------------------------------- #

def _handle_login_step(app, chat_id, message: dict) -> None:
    """Consome uma mensagem enquanto o login está em andamento."""
    with app.app_context():
        stage = link.login_stage(chat_id)
        text = (message.get("text") or "").strip()
        if stage == "email":
            if not text:
                api.send_message(chat_id, "Manda seu email, por favor:")
                return
            link.set_login_email(chat_id, text)
            api.send_message(
                chat_id,
                "Agora a senha 🔒 (vou apagar a mensagem dela em seguida, por segurança):",
            )
        elif stage == "password":
            # apaga a mensagem com a senha o quanto antes
            api.delete_message(chat_id, message.get("message_id"))
            email = link.pop_login_email(chat_id)
            user = link.authenticate(email or "", text)
            if user is None:
                api.send_message(chat_id, "❌ Email ou senha inválidos. Tente /login de novo.")
                return
            link.link_chat(chat_id, user.id)
            nome = user.name or user.email or "você"
            api.send_message(chat_id, f"✅ Logado como {nome}! Pode falar comigo normalmente 💙")
            # sincroniza o contexto: mostra as últimas mensagens da conversa
            _send_history(app, chat_id, user.id, limit=10,
                          header="Pra retomar de onde parou, aqui estão as últimas mensagens:")


def _handle_command(app, chat_id, cmd: str, message: dict) -> bool:
    """Trata /comandos. Devolve True se consumiu a mensagem."""
    with app.app_context():
        cmd = cmd.lower()
        if cmd in ("/start", "/help"):
            uid = link.user_id_for_chat(chat_id)
            api.send_message(chat_id, _HELP if uid is None else "Tô aqui! É só mandar sua mensagem 💙")
            return True
        if cmd == "/login":
            if link.user_id_for_chat(chat_id) is not None:
                api.send_message(chat_id, "Você já está logado 🙂 (use /logout pra trocar de conta)")
                return True
            link.start_login(chat_id)
            api.send_message(chat_id, "Bora! Qual o email da sua conta?")
            return True
        if cmd == "/cancel":
            link.cancel_login(chat_id)
            api.send_message(chat_id, "Ok, cancelei.")
            return True
        if cmd == "/logout":
            link.cancel_login(chat_id)
            api.send_message(
                chat_id,
                "Pronto, desvinculei este chat. 👋" if link.unlink_chat(chat_id)
                else "Este chat não estava vinculado.",
            )
            return True
        if cmd == "/whoami":
            uid = link.user_id_for_chat(chat_id)
            if uid is None:
                api.send_message(chat_id, "Você não está logado. Use /login.")
            else:
                from app.models import User
                u = db.session.get(User, uid)
                api.send_message(chat_id, f"Logado como {u.name or u.email or uid}.")
            return True
        if cmd == "/historico":
            uid = link.user_id_for_chat(chat_id)
            if uid is None:
                api.send_message(chat_id, "Você não está logado. Use /login.")
                return True
            _send_history(app, chat_id, uid, limit=20)
            return True
    return False


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

def _dispatch_message(app, pool, message: dict) -> None:
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return
    text = message.get("text") or message.get("caption") or ""

    # /comandos primeiro
    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0]
        if _handle_command(app, chat_id, cmd, message):
            return

    # login em andamento consome a mensagem
    with app.app_context():
        in_login = link.login_stage(chat_id) is not None
        user_id = link.user_id_for_chat(chat_id)
    if in_login:
        _handle_login_step(app, chat_id, message)
        return

    if user_id is None:
        with app.app_context():
            api.send_message(chat_id, "Você ainda não fez login. Use /login pra começar 🙂")
        return

    # logado → processa o turno (fora do poller, pra não travar o polling)
    pool.submit(_process_turn, app, chat_id, user_id, text, message)


def _dispatch_callback(app, callback: dict) -> None:
    """Botões (aprovação de shell)."""
    from app.blueprints.commands import apply_shell_decision

    cq_id = callback.get("id")
    data = callback.get("data") or ""
    message = callback.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    msg_id = message.get("message_id")

    if not data.startswith("shell:"):
        api.answer_callback_query(cq_id)
        return
    try:
        _, cmd_id, decision = data.split(":", 2)
        cmd_id = int(cmd_id)
    except (ValueError, TypeError):
        api.answer_callback_query(cq_id, "ação inválida")
        return

    with app.app_context():
        user_id = link.user_id_for_chat(chat_id)
        if user_id is None:
            api.answer_callback_query(cq_id, "faça /login primeiro")
            return
        _msgs, err, _status = apply_shell_decision(user_id, cmd_id, decision)

    label = {"allow": "permitido", "deny": "negado", "always": "permitido (sempre)"}.get(decision, "ok")
    api.answer_callback_query(cq_id, err or label)
    api.edit_reply_markup(chat_id, msg_id, None)  # tira os botões
    # a saída do comando + resposta da Helena chegam via emit_new_messages
    # (dentro de apply_shell_decision) → delivery pro Telegram.


# --------------------------------------------------------------------------- #
# Loop principal
# --------------------------------------------------------------------------- #

def run_bot(app) -> None:
    timeout = app.config.get("TELEGRAM_POLL_TIMEOUT", 50)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tg-turn")

    # checa o token uma vez (log amigável se estiver errado)
    with app.app_context():
        try:
            me = api.get_me()
            app.logger.info("Telegram: conectado como @%s", me.get("username"))
        except api.TelegramError as exc:
            app.logger.warning("Telegram: token inválido? %s — bot não vai receber updates.", exc)

    offset = None
    while True:
        try:
            with app.app_context():
                updates = api.get_updates(offset, timeout)
        except api.TelegramError as exc:
            app.logger.warning("telegram getUpdates: %s", exc)
            time.sleep(3)
            continue
        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                if "message" in upd:
                    _dispatch_message(app, pool, upd["message"])
                elif "callback_query" in upd:
                    _dispatch_callback(app, upd["callback_query"])
            except Exception as exc:  # noqa: BLE001 — nunca derruba o poller
                app.logger.warning("telegram: erro no update: %s", exc)
