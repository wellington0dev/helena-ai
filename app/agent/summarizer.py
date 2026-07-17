"""Resumo rolante incremental (CLAUDE.md §5).

Mantém o contexto limitado: as últimas CHAT_WINDOW mensagens seguem cruas; tudo
que envelhece para fora dessa janela é fundido (não substituído) no
`conversation_summary`. Roda inline após o turno do agente. Pode migrar para um
`job` na Fase 5 sem mudar a lógica.
"""
from sqlalchemy import select

from app.agent import llm
from app.extensions import db, write_lock
from app.models import ConversationSummary, Message

_SUMMARY_INSTRUCTION = """\
Você mantém um resumo rolante da relação entre a Helena e o usuário. Recebe o \
RESUMO ATUAL (pode estar vazio) e um LOTE de mensagens antigas que estão saindo \
da janela de contexto. Funda os dois num único resumo atualizado, em português, \
que preserve fatos importantes, decisões, gostos, metas e o fio da conversa. \
Seja conciso mas não perca informação relevante. Não invente nada. Responda \
apenas com o texto do resumo, sem preâmbulo."""


def _oldest_in_window_id(user_id: int, window: int) -> int | None:
    """Id da mensagem mais antiga ainda dentro da janela das últimas `window`."""
    rows = db.session.scalars(
        select(Message.id)
        .where(Message.user_id == user_id)
        .order_by(Message.id.desc())
        .limit(window)
    ).all()
    if len(rows) < window:
        return None  # ainda cabe tudo na janela; nada a resumir
    return min(rows)


def maybe_summarize(user_id: int, window: int) -> bool:
    """Resume o lote que saiu da janela, se houver ao menos `window` mensagens
    fora dela ainda não resumidas. Retorna True se resumiu."""
    cutoff = _oldest_in_window_id(user_id, window)
    if cutoff is None:
        return False

    summary_row = db.session.get(ConversationSummary, user_id)
    last_id = summary_row.last_summarized_msg_id if summary_row else None

    stmt = select(Message).where(
        Message.user_id == user_id, Message.id < cutoff
    )
    if last_id is not None:
        stmt = stmt.where(Message.id > last_id)
    stmt = stmt.order_by(Message.id.asc())
    batch = db.session.scalars(stmt).all()

    # só dispara em lotes de ~window para não resumir a cada mensagem
    if len(batch) < window:
        return False

    prev_summary = summary_row.summary if summary_row else ""
    lote_txt = "\n".join(
        f"{m.role}: {m.content}" for m in batch if m.content
    )
    user_prompt = (
        f"RESUMO ATUAL:\n{prev_summary or '(vazio)'}\n\n"
        f"LOTE DE MENSAGENS ANTIGAS:\n{lote_txt}"
    )

    new_summary = llm.generate_text(_SUMMARY_INSTRUCTION, user_prompt).strip()
    if not new_summary:
        return False

    max_id = batch[-1].id
    with write_lock:
        row = db.session.get(ConversationSummary, user_id)
        if row is None:
            row = ConversationSummary(user_id=user_id)
            db.session.add(row)
        row.summary = new_summary
        row.last_summarized_msg_id = max_id
        db.session.commit()
    return True
