"""Orquestra um turno do agente: da mensagem do usuário às respostas persistidas.

Um turno pode gerar VÁRIAS mensagens do assistant: cada tool de geração
(imagem, áudio, documento) insere sua própria `messages` no meio do loop, e o
texto final vira mais uma. `handle_user_turn` devolve todas em ordem cronológica.
"""
from flask import current_app
from sqlalchemy import select

from app.agent import gemini, summarizer
from app.agent.shell_tool import reset_shell_budget
from app.extensions import db, write_lock
from app.models import Message


def handle_user_turn(user_id: int, since_msg_id: int) -> list[Message]:
    """Roda o agente (mensagem do usuário já persistida, id=`since_msg_id`),
    persiste as respostas do assistant, dispara o resumo rolante e devolve todas
    as mensagens do assistant criadas neste turno (mídia + texto final)."""
    cfg = current_app.config
    api_key = cfg["GEMINI_API_KEY"]
    model = cfg["GEMINI_MODEL"]
    reset_shell_budget()  # zera o orçamento de shell por turno

    def _run():
        return gemini.run_agent(
            user_id=user_id,
            api_key=api_key,
            model=model,
            max_iters=cfg["MAX_TOOL_ITERATIONS"],
        )

    reply_text, tool_fired = _run()

    # No-op puro: o modelo não chamou nenhuma tool E não escreveu texto (ex.:
    # o usuário pediu uma imagem e o modelo "engasgou"). Como nada foi feito,
    # é seguro retentar uma vez — uma nova amostragem costuma converter isso
    # numa geração de verdade. NUNCA retenta se alguma tool já disparou, senão
    # duplicaria efeitos (job em dobro, lembrete repetido).
    if not tool_fired and not reply_text:
        reply_text, tool_fired = _run()

    # só persiste texto final se houver — turnos que só geram mídia podem
    # não ter texto adicional
    if reply_text:
        with write_lock:
            msg = Message(user_id=user_id, role="assistant", content=reply_text)
            db.session.add(msg)
            db.session.commit()

    def _collect() -> list[Message]:
        return db.session.scalars(
            select(Message)
            .where(
                Message.user_id == user_id,
                Message.role == "assistant",
                Message.id > since_msg_id,
            )
            .order_by(Message.id.asc())
        ).all()

    # coleta tudo que o assistant criou neste turno (mídia de tools + texto)
    replies = _collect()

    # Rede de segurança: se o turno não produziu NENHUMA mensagem do assistant,
    # persiste um fecho curto. Sem isso o histórico fica com duas mensagens de
    # `user` seguidas e no próximo turno o modelo reprocessa/redispara a última.
    # O texto depende do que aconteceu:
    #  - tool disparou sem texto de fecho (lembrete/job/nota) → confirmação;
    #  - nada disparou (no-op mesmo após retry) → não finge sucesso: pede pra
    #    repetir, pra não dizer "feito!" quando nada foi entregue.
    if not replies:
        fallback = "Prontinho! 👍" if tool_fired else (
            "Opa, me embananei aqui 😅 tenta mandar de novo?"
        )
        with write_lock:
            db.session.add(
                Message(user_id=user_id, role="assistant", content=fallback)
            )
            db.session.commit()
        replies = _collect()

    # resumo rolante (inline por ora; pode virar job na Fase 5)
    try:
        summarizer.maybe_summarize(
            user_id=user_id,
            api_key=api_key,
            model=model,
            window=cfg["CHAT_WINDOW"],
        )
    except Exception as exc:  # noqa: BLE001 — resumo não deve derrubar a resposta
        current_app.logger.warning("resumo rolante falhou: %s", exc)

    return replies
