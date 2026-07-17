"""Worker de background jobs: poller + thread pool (CLAUDE.md §8).

Iniciado no run.py (fora de create_app, que roda nos testes). Cuidados:
- transação fresca por ciclo (`db.session.remove()`), senão o poller para de
  enxergar jobs recém-commitados por outra thread;
- claim atômico sob write_lock (UPDATE ... WHERE status='pending');
- execução (Gemini, lenta) FORA do lock; só o insert do resultado sob o lock;
- cada job roda no próprio app_context com `db.session.remove()` no finally.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

from flask import current_app

from app.agent import llm
from app.extensions import db, socketio, write_lock
from app.federation.client import FederationError, send_message
from app.jobs import executors
from app.models import Job, Message, NotificationQueue, Peer, PeerMessage, User
from app.realtime import emit_job_done, emit_job_progress, emit_peer_message
from app.agenda.timeutil import now_utc

_wake = threading.Event()
_started = False


def request_wake() -> None:
    """Acorda o poller (chamado por run_background_job para não esperar o poll)."""
    _wake.set()


def _claim(job_id: int) -> bool:
    """Marca o job como running de forma atômica. True se este thread pegou."""
    with write_lock:
        rows = (
            db.session.query(Job)
            .filter(Job.id == job_id, Job.status == "pending")
            .update({Job.status: "running"}, synchronize_session=False)
        )
        db.session.commit()
    return rows == 1


def _job_done_body(title: str, result: str) -> str:
    """Corpo curto da notificação, escrito pela IA. Best-effort com fallback."""
    try:
        text = llm.generate_text(
            "Escreva UMA frase curta e casual (como a Helena) avisando que "
            "terminou a tarefa. Sem aspas. Responda só a frase.",
            f"Tarefa: {title}\n\nPrévia do resultado:\n{result[:500]}",
        )
        return text or f"Terminei: {title}"
    except Exception:  # noqa: BLE001
        return f"Terminei: {title}"


def _complete_federation_reply(app, job_id: int) -> None:
    """Conclusão especializada pra job type=federation_reply — NÃO é chat-facing:
    não cria Message nem NotificationQueue(job_done), não chama emit_job_done.
    Cria uma PeerMessage de saída (authored_by=ai) e reusa emit_peer_message."""
    with app.app_context():
        try:
            job = db.session.get(Job, job_id)
            if job is None:
                return
            cfg = current_app.config
            peer_id = (job.payload or {}).get("peer_id")
            peer = db.session.get(Peer, peer_id)
            user = db.session.get(User, job.user_id) if peer is not None else None

            # rechecagem na hora da execução: panic/trust_level/toggle podem ter
            # mudado desde o enqueue. Se não se aplica mais, não é um erro.
            if (
                peer is None
                or not peer.ai_dialogue_enabled
                or peer.trust_level == "nao_confiavel"
                or (user is not None and user.federation_paused)
                or peer.ai_turn_streak >= cfg["FEDERATION_MAX_AI_TURNS"]
            ):
                with write_lock:
                    job = db.session.get(Job, job_id)
                    job.status = "done"
                    db.session.commit()
                return

            # resolve o kind da mensagem que disparou este job — Fase 3: se
            # foi um help_request, a resposta sai kind=help_response com
            # in_reply_to correto; senão continua kind=chat (Fase 2, sem
            # mudança). Nenhuma fonte de dado nova, só tagging de saída.
            trigger_msg_id = (job.payload or {}).get("message_id")
            trigger = db.session.get(PeerMessage, trigger_msg_id) if trigger_msg_id else None
            responding_to_help_request = bool(trigger and trigger.kind == "help_request" and trigger.request_id)
            out_kind = "help_response" if responding_to_help_request else "chat"
            out_in_reply_to = trigger.request_id if responding_to_help_request else None

            reply_text = executors._federation_reply(
                job, responding_to_kind=out_kind if responding_to_help_request else "chat"
            )
            if not reply_text:
                with write_lock:
                    job = db.session.get(Job, job_id)
                    job.status = "done"
                    db.session.commit()
                return

            # streak sobe no ENVIO (mesmo se a entrega falhar abaixo) — o teto é
            # sobre custo/geração nossa, não sobre confirmação do outro lado.
            with write_lock:
                peer = db.session.get(Peer, peer_id)
                msg = PeerMessage(
                    peer_id=peer.id, user_id=peer.user_id, direction="outgoing",
                    body=reply_text, authored_by="ai", status="pending",
                    kind=out_kind, in_reply_to=out_in_reply_to,
                )
                db.session.add(msg)
                peer.ai_turn_streak = (peer.ai_turn_streak or 0) + 1
                db.session.commit()
                msg_id = msg.id
                owner_id = peer.user_id

            try:
                peer = db.session.get(Peer, peer_id)
                send_message(peer, reply_text, kind=out_kind, in_reply_to=out_in_reply_to)
                ok = True
            except FederationError as exc:
                ok = False
                app.logger.warning(
                    "federação: falha ao enviar resposta automática pro peer %s: %s", peer_id, exc
                )

            with write_lock:
                msg = db.session.get(PeerMessage, msg_id)
                msg.status = "sent" if ok else "failed"
                job = db.session.get(Job, job_id)
                job.status = "done" if ok else "error"
                if not ok:
                    job.error = "falha ao entregar resposta automática"
                db.session.commit()
                msg_dict = msg.to_dict()

            emit_peer_message(owner_id, msg_dict)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("job federation_reply %s falhou: %s", job_id, exc)
            with write_lock:
                job = db.session.get(Job, job_id)
                if job is not None:
                    job.status = "error"
                    job.error = str(exc)
                    db.session.commit()
        finally:
            db.session.remove()


def _run_job(app, job_id: int) -> None:
    with app.app_context():
        job = db.session.get(Job, job_id)
        job_type = job.type if job is not None else None
        db.session.remove()

    if job_type == "federation_reply":
        _complete_federation_reply(app, job_id)
        return

    with app.app_context():
        try:
            job = db.session.get(Job, job_id)
            if job is None:
                return
            title = (job.payload or {}).get("title") or (job.payload or {}).get("query") or "sua tarefa"
            user_id = job.user_id

            # feedback contínuo ao vivo (WebSocket) enquanto o loop trabalha
            def _progress(text: str) -> None:
                emit_job_progress(user_id, text)

            # execução lenta (Gemini) FORA do write_lock
            result_text = executors.execute_job(job, on_progress=_progress)
            body = _job_done_body(title, result_text)

            with write_lock:
                msg = Message(user_id=job.user_id, role="assistant", content=result_text)
                db.session.add(msg)
                db.session.flush()  # garante msg.id
                job.status = "done"
                job.result_ref = str(msg.id)
                db.session.add(
                    NotificationQueue(
                        user_id=job.user_id,
                        title=title,
                        body=body,
                        fire_at=now_utc(),
                        type="job_done",
                        reference_id=job.id,
                    )
                )
                db.session.commit()
                msg_dict = msg.to_dict()

            # app aberto → empurra em tempo real; app fechado → notification_queue
            emit_job_done(job.user_id, msg_dict)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("job %s falhou: %s", job_id, exc)
            with write_lock:
                job = db.session.get(Job, job_id)
                if job is not None:
                    job.status = "error"
                    job.error = str(exc)
                    db.session.commit()
        finally:
            db.session.remove()


def start_worker(app, poll_interval: float = 3.0, max_workers: int = 2):
    """Sobe o poller (daemon) que reivindica jobs pending e os despacha ao pool.
    Idempotente."""
    global _started
    if _started:
        return
    _started = True
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")

    def _poller():
        while True:
            _wake.wait(timeout=poll_interval)
            _wake.clear()
            try:
                with app.app_context():
                    db.session.remove()  # transação fresca: enxerga novos commits
                    ids = [
                        j.id
                        for j in db.session.query(Job)
                        .filter(Job.status == "pending")
                        .order_by(Job.id.asc())
                        .all()
                    ]
                    claimed = [jid for jid in ids if _claim(jid)]
                    db.session.remove()
                for jid in claimed:
                    pool.submit(_run_job, app, jid)
            except Exception as exc:  # noqa: BLE001 — poller nunca deve morrer
                app.logger.warning("poller de jobs: %s", exc)

    threading.Thread(target=_poller, name="job-poller", daemon=True).start()
    return pool
