"""Gera o texto (body) da notificação — casual e contextual, escrito pela IA.

Best-effort: se o Gemini falhar, cai num fallback simples (CLAUDE.md §9).
"""
from flask import current_app

from app.models import Reminder, UserProfile
from app.extensions import db

_STAGE_LABEL = {
    "1w": "falta uma semana",
    "1d": "falta um dia",
    "6h": "faltam 6 horas",
    "simple": "está na hora",
    "recorrente": "lembrete recorrente, está na hora",
}

_INSTRUCTION = (
    "Você é a Helena. Escreva o texto de UMA notificação push curta (máx. ~140 "
    "caracteres), casual e calorosa, lembrando o usuário de um compromisso. "
    "Sem aspas, sem emojis em excesso, direto ao ponto. Responda só o texto."
)


def _fallback(reminder: Reminder, stage: str) -> str:
    return f"Lembrete: {reminder.title}"


def generate_body(reminder: Reminder, stage: str, when=None) -> str:
    """Texto da notificação para uma etapa. Nunca levanta — usa fallback.
    `when` (opcional) = momento da ocorrência (usado nos recorrentes)."""
    try:
        from app.agent import llm  # import tardio (evita ciclo)

        nome = None
        prof = db.session.get(UserProfile, reminder.user_id)
        if prof and prof.profile:
            nome = prof.profile.get("nome_preferido")

        quando = (when or reminder.due_at).astimezone().strftime("%d/%m %H:%M")
        contexto = (
            f"Compromisso: {reminder.title}\n"
            f"Detalhes: {reminder.description or '(sem detalhes)'}\n"
            f"Quando: {quando}\n"
            f"Etapa do lembrete: {_STAGE_LABEL.get(stage, stage)}\n"
            f"Nome do usuário: {nome or 'não informado'}"
        )
        text = llm.generate_text(_INSTRUCTION, contexto)
        return text or _fallback(reminder, stage)
    except Exception as exc:  # noqa: BLE001
        current_app.logger.warning("gera body de notificação falhou: %s", exc)
        return _fallback(reminder, stage)
