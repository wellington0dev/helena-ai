"""Execução AGENDADA de rotinas (roda sozinha, sem humano no loop).

Segurança (o scheduler roda numa thread sem usuário/JWT — o gate vai na hora do
disparo, contra o estado ATUAL do dono):
- só executa rotina AUTORADA pelo usuário (`created_by='user'`) — nunca ai-authored
  (senão a IA agendaria shell autônomo sem aprovação);
- só se o dono AINDA for principal+ (panic/rebaixamento param o agendamento na hora).

Coalesce: se perdeu vários disparos (máquina dormindo), roda UMA vez e avança para
a próxima ocorrência futura — não dispara a fila de atrasados.
"""
from flask import current_app

from app.agenda import scan
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.models import Message, NotificationQueue, Routine


def _notify(routine: Routine) -> None:
    from app.realtime import emit_new_messages

    with write_lock:
        msg = Message(
            user_id=routine.user_id, role="assistant",
            content=f"Rodei sua rotina agendada: {routine.name} ✅",
        )
        db.session.add(msg)
        db.session.flush()
        msg_dict = msg.to_dict()
        db.session.add(NotificationQueue(
            user_id=routine.user_id, title=routine.name,
            body=f"Rodei sua rotina '{routine.name}'", fire_at=now_utc(),
            type="reminder", reference_id=routine.id,
        ))
        db.session.commit()
    emit_new_messages(routine.user_id, [msg_dict])


def run_one(routine: Routine) -> bool:
    """Gate de disparo + execução. True se de fato executou."""
    from app.agent.automations_tools import _build_script
    from app.agent.shell_tool import run_direct, shell_level

    if routine.created_by != "user":
        return False  # IA não agenda shell autônomo
    if shell_level(routine.user_id) is None:
        return False  # dono rebaixado / panic → não roda
    script, _missing = _build_script(routine.user_id, routine)
    if not script.strip():
        return False
    run_direct(routine.user_id, script)  # executa + audita + persiste saída no chat
    _notify(routine)
    return True


def _advance(routine: Routine, now) -> None:
    with write_lock:
        if routine.recurrence in scan.RECURRENCES:
            nxt = routine.next_run
            guard = 0
            while nxt is not None and nxt <= now and guard < 1000:
                nxt = scan.next_occurrence(nxt, routine.recurrence)
                guard += 1
            routine.next_run = nxt
        else:
            routine.enabled = False  # disparo único → desativa
        db.session.commit()


def run_due_routines(now=None) -> int:
    """Executa as rotinas agendadas que venceram. Chamado pelo scheduler."""
    now = now or now_utc()
    due = (
        db.session.query(Routine)
        .filter(Routine.enabled.is_(True), Routine.next_run.isnot(None), Routine.next_run <= now)
        .all()
    )
    ran = 0
    for r in due:
        try:
            if run_one(r):
                ran += 1
        except Exception as exc:  # noqa: BLE001 — uma rotina ruim não trava o resto
            current_app.logger.warning("rotina agendada %s falhou: %s", r.id, exc)
            db.session.rollback()
        _advance(r, now)  # coalesce mesmo se pulou/falhou
    return ran
