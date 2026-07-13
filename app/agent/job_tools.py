"""Tool run_background_job: dispara trabalho longo sem bloquear o chat (§8)."""
from app.extensions import db, write_lock
from app.jobs import worker
from app.models import Job

_ALLOWED = {"research", "plan"}


def run_background_job(user_id: int, args: dict) -> dict:
    job_type = (args.get("type") or "").strip()
    if job_type not in _ALLOWED:
        return {"ok": False, "error": f"type deve ser um de {sorted(_ALLOWED)}"}
    payload = args.get("payload")
    if not isinstance(payload, dict) or not payload:
        return {"ok": False, "error": "payload (objeto) obrigatório"}

    with write_lock:
        job = Job(user_id=user_id, type=job_type, payload=payload, status="pending")
        db.session.add(job)
        db.session.commit()
        jid = job.id

    worker.request_wake()  # acorda o poller sem esperar o intervalo
    return {
        "ok": True,
        "job_id": jid,
        "status": "trabalhando nisso — te aviso quando terminar",
    }
