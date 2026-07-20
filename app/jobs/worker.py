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

from app.agent import llm
from app.extensions import db, write_lock
from app.jobs import executors
from app.models import Job, Message, NotificationQueue
from app.realtime import emit_job_done, emit_job_progress
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


def _run_job(app, job_id: int) -> None:
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
