"""Scan de reminders → enfileira notification_queue (CLAUDE.md §9).

Etapas de um lembrete `agenda`: 1 semana / 1 dia / 6 horas antes (cada uma
dispara uma vez). `simple`: 1 disparo em `notify_at`. Cada etapa vira um item
de `notification_queue` com `fire_at` = momento da etapa e `body` escrito pela IA.
O celular puxa a fila (próximas 24h) e materializa localmente.
"""
import calendar
from datetime import timedelta

from flask import current_app

from app.agenda import notify_body
from app.agenda.timeutil import now_utc
from app.extensions import db, write_lock
from app.models import NotificationQueue, Reminder

RECURRENCES = {"daily", "weekly", "monthly", "yearly"}


def next_occurrence(dt, recurrence):
    """Avança um datetime para a próxima ocorrência da recorrência."""
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "monthly":
        month = dt.month + 1
        year = dt.year + (month > 12)
        month = 1 if month > 12 else month
        day = min(dt.day, calendar.monthrange(year, month)[1])  # clampa (ex.: 31→30)
        return dt.replace(year=year, month=month, day=day)
    if recurrence == "yearly":
        try:
            return dt.replace(year=dt.year + 1)
        except ValueError:  # 29/02 em ano não bissexto
            return dt.replace(year=dt.year + 1, day=28)
    return dt + timedelta(days=1)  # fallback defensivo

# Enfileira etapas cujo momento está dentro deste horizonte à frente.
# 27h > 24h (janela que o celular puxa) + 3h (intervalo do cron) → cobertura garantida.
HORIZON = timedelta(hours=27)
# Etapa que passou há menos disto ainda dispara "agora" (cobre o gap entre ticks).
GRACE = timedelta(hours=3)


def _stages(reminder: Reminder):
    """(stage_key, momento, nome_da_flag) de cada etapa pendente do lembrete."""
    if reminder.kind == "agenda":
        due = reminder.due_at
        return [
            ("1w", due - timedelta(weeks=1), "notified_1w"),
            ("1d", due - timedelta(days=1), "notified_1d"),
            ("6h", due - timedelta(hours=6), "notified_6h"),
        ]
    # simple: disparo único (notify_at, ou due_at se ausente)
    return [("simple", reminder.notify_at or reminder.due_at, "notified")]


def enqueue_for_reminder(reminder: Reminder, now=None) -> int:
    """Enfileira as etapas do lembrete que caíram na janela. Retorna quantas.

    Nota: a checagem da flag (topo do loop) e o set (sob o lock) não são atômicos
    entre threads/sessões distintas — cron e scan inline de create_reminder podem,
    numa janela de segundos, enfileirar a mesma etapa em dobro (impacto: 1 push
    duplicado). Aceitável no cenário single-process (§2). Fix durável futuro:
    re-ler a flag já sob o lock antes de inserir.
    """
    now = now or now_utc()
    if reminder.recurrence in RECURRENCES:
        return _enqueue_recurring(reminder, now)
    count = 0
    for stage, when, flag in _stages(reminder):
        if getattr(reminder, flag):
            continue  # já enfileirada
        if when > now + HORIZON:
            continue  # ainda cedo; um scan futuro pega
        if when < now - GRACE:
            # etapa velha demais: marca como tratada, não notifica atrasado
            with write_lock:
                setattr(reminder, flag, True)
                db.session.commit()
            continue

        # gera o body FORA do write_lock (chamada ao Gemini é lenta)
        body = notify_body.generate_body(reminder, stage)
        fire_at = when if when > now else now  # etapa recém-passada dispara já

        with write_lock:
            db.session.add(
                NotificationQueue(
                    user_id=reminder.user_id,
                    title=reminder.title,
                    body=body,
                    fire_at=fire_at,
                    type="reminder",
                    reference_id=reminder.id,
                )
            )
            setattr(reminder, flag, True)  # atômico com o insert
            db.session.commit()
            count += 1
    return count


def _enqueue_recurring(reminder: Reminder, now) -> int:
    """Enfileira as ocorrências de um lembrete recorrente que caem na janela e
    avança `notify_at`/`due_at` para a próxima ocorrência FORA da janela — assim
    cada ocorrência é enfileirada uma única vez, e o lembrete nunca "conclui"."""
    count = 0
    when = reminder.notify_at or reminder.due_at
    guard = 0
    # pula ocorrências velhas demais (não notifica atrasado); catch-up limitado
    while when < now - GRACE and guard < 1000:
        when = next_occurrence(when, reminder.recurrence)
        guard += 1
    # enfileira todas as ocorrências dentro do horizonte (pode ser >1 p/ daily)
    while when <= now + HORIZON and guard < 1000:
        body = notify_body.generate_body(reminder, "recorrente", when=when)
        fire_at = when if when > now else now
        with write_lock:
            db.session.add(
                NotificationQueue(
                    user_id=reminder.user_id,
                    title=reminder.title,
                    body=body,
                    fire_at=fire_at,
                    type="reminder",
                    reference_id=reminder.id,
                )
            )
            db.session.commit()
        count += 1
        when = next_occurrence(when, reminder.recurrence)
        guard += 1
    # guarda a próxima ocorrência (ainda fora do horizonte) para o próximo scan
    with write_lock:
        reminder.notify_at = when
        reminder.due_at = when
        reminder.notified = False
        db.session.commit()
    return count


def scan_and_enqueue(now=None) -> int:
    """Varre lembretes não totalmente notificados e enfileira o que estiver na
    janela. Chamado pelo cron a cada 3h. Retorna total enfileirado."""
    now = now or now_utc()
    # candidatos: due_at recente/futuro e ainda com alguma etapa pendente.
    # Filtra por due_at (não notify_at); ok hoje porque `simple` tem
    # notify_at = due_at por padrão. Se um dia divergirem muito, incluir
    # também notify_at no filtro.
    reminders = (
        db.session.query(Reminder)
        .filter(Reminder.due_at >= now - timedelta(days=1))
        .all()
    )
    total = 0
    for r in reminders:
        fully = (
            (r.notified_1w and r.notified_1d and r.notified_6h)
            if r.kind == "agenda"
            else r.notified
        )
        if fully:
            continue
        try:
            total += enqueue_for_reminder(r, now)
        except Exception as exc:  # noqa: BLE001 — um lembrete ruim não trava o scan
            current_app.logger.warning("scan do reminder %s falhou: %s", r.id, exc)
            db.session.rollback()
    return total
