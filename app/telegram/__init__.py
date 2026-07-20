"""Bot do Telegram — a Helena como cliente completo (login por email+senha).

Só sobe se `TELEGRAM_BOT_TOKEN` estiver configurado. Long-polling em thread
daemon (não precisa de URL pública/webhook).
"""
import threading

_started = False


def start_telegram_bot(app) -> None:
    """Inicia o bot em background, se houver token. Idempotente."""
    global _started
    if _started:
        return
    if not app.config.get("TELEGRAM_BOT_TOKEN"):
        app.logger.info("Telegram: sem TELEGRAM_BOT_TOKEN — bot desligado.")
        return
    _started = True

    from app.telegram.bot import run_bot

    threading.Thread(target=run_bot, args=(app,), name="telegram-bot", daemon=True).start()
    app.logger.info("Telegram: bot iniciando (long-polling).")
