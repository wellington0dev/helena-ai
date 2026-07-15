"""APScheduler embarcado — varre a agenda a cada 3h (CLAUDE.md §9).

Iniciado a partir do `run.py` (nunca em `create_app`, que roda nos testes).
O job empurra o contexto de app para acessar o banco.
"""
from apscheduler.schedulers.background import BackgroundScheduler

from app.agenda.scan import scan_and_enqueue

_scheduler: BackgroundScheduler | None = None


def start_scheduler(app) -> BackgroundScheduler:
    """Cria e inicia o scheduler com o job de 3h. Idempotente.

    Assume processo único (CLAUDE.md §2). Se um dia rodar com múltiplos workers,
    cada worker subiria um scheduler → scans duplicados; nesse caso seria preciso
    eleição de líder ou mover o job para fora dos workers.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(daemon=True)

    def _job():
        with app.app_context():
            n = scan_and_enqueue()
            if n:
                app.logger.info("scan da agenda enfileirou %s notificações", n)

    # primeira execução em ~3h (default do interval); o scan inline dentro de
    # create_reminder cobre lembretes imediatos
    scheduler.add_job(
        _job,
        trigger="interval",
        hours=3,
        id="agenda_scan",
        replace_existing=True,
    )

    from app.agenda.routine_scheduler import run_due_routines

    def _routines_job():
        with app.app_context():
            n = run_due_routines()
            if n:
                app.logger.info("scheduler executou %s rotina(s) agendada(s)", n)

    scheduler.add_job(
        _routines_job,
        trigger="interval",
        minutes=5,
        id="routine_scheduler",
        replace_existing=True,
        coalesce=True,           # se perdeu ticks, roda 1x só
        misfire_grace_time=300,
    )

    from app.federation.cleanup import purge_expired_nonces

    def _federation_nonce_cleanup_job():
        with app.app_context():
            n = purge_expired_nonces()
            if n:
                app.logger.info("federação: purgou %s nonce(s) expirado(s)", n)

    scheduler.add_job(
        _federation_nonce_cleanup_job,
        trigger="interval",
        minutes=15,
        id="federation_nonce_cleanup",
        replace_existing=True,
        coalesce=True,
        misfire_grace_time=300,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler
