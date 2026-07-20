"""WebSocket (Socket.IO): autenticação por JWT, rooms por usuário e emits.

O app aberto recebe resultados de background jobs em tempo real; o app fechado
pega pela notification_queue (CLAUDE.md §8). Cada usuário entra numa room com o
próprio id; o worker emite para essa room.
"""
from flask_jwt_extended import decode_token
from flask_socketio import join_room

from app.extensions import db, socketio
from app.models import User


@socketio.on("connect")
def _on_connect(auth):
    """Autentica o socket pelo token no payload `auth`. Recusa se inválido ou
    se o usuário não existe mais (mesmo espírito do user_lookup no REST)."""
    token = (auth or {}).get("token")
    if not token:
        return False
    try:
        uid = int(decode_token(token)["sub"])
    except Exception:  # noqa: BLE001 — token inválido/expirado
        return False
    if db.session.get(User, uid) is None:
        return False
    join_room(str(uid))
    return True


def _to_telegram(user_id: int, messages: list[dict]) -> None:
    """Espelha mensagens nos chats vinculados do Telegram (best-effort). Import
    tardio evita ciclo (telegram → commands → realtime)."""
    try:
        from app.telegram.delivery import deliver_messages
        deliver_messages(user_id, messages)
    except Exception:  # noqa: BLE001 — entrega ao Telegram nunca derruba o emit
        pass


def emit_job_done(user_id: int, message: dict) -> None:
    """Empurra o resultado de um job para o dono (se estiver com o app aberto)."""
    socketio.emit("job_done", {"message": message}, room=str(user_id))
    _to_telegram(user_id, [message])


def emit_new_messages(user_id: int, messages: list[dict]) -> None:
    """Empurra mensagens novas para o app aberto (§13). Cobre respostas vindas
    da notificação (RemoteInput), que não passam pela WebView. O cliente faz
    dedupe por id, então a própria sessão que enviou não duplica."""
    socketio.emit("new_messages", {"messages": messages}, room=str(user_id))
    _to_telegram(user_id, messages)


def emit_job_progress(user_id: int, text: str) -> None:
    """Feedback contínuo de um job iterativo (efêmero, ao vivo — só com o app
    aberto). Não é persistido nem entra no contexto do agente: é só o usuário
    acompanhando o trabalho em andamento."""
    socketio.emit("job_progress", {"text": text}, room=str(user_id))
