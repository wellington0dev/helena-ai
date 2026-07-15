"""Tool run_background_job: dispara trabalho longo sem bloquear o chat (§8)."""
from app.extensions import db, write_lock
from app.jobs import worker
from app.models import Job

_ALLOWED = {"research", "plan", "desktop_task"}
# desktop_task age de verdade no computador do usuário — mesmo nível do
# controle de mouse/teclado no chat. Checado aqui de novo (defesa em
# profundidade): a única tool exposta que dispara isso já é fullcontrol-only,
# mas isso protege qualquer outro chamador futuro do mesmo bug.
_REQUIRES_FULL_CONTROL = {"desktop_task"}


def run_background_job(user_id: int, args: dict) -> dict:
    job_type = (args.get("type") or "").strip()
    if job_type not in _ALLOWED:
        return {"ok": False, "error": f"type deve ser um de {sorted(_ALLOWED)}"}
    if job_type in _REQUIRES_FULL_CONTROL:
        from app.agent.shell_tool import shell_level

        if shell_level(user_id) != "full":
            return {"ok": False, "error": "essa tarefa exige controle absoluto do computador"}
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
