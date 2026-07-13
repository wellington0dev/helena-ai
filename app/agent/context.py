"""Montagem de contexto (CLAUDE.md §5) — o coração do agente.

Concatena 4 fontes mantidas separadas para não incharem nem se contradizerem:
  SYSTEM PROMPT (fixo) + user_profile + ai_notes + conversation_summary
  + contexto dinâmico (nome, data/hora) + últimas N mensagens.
"""
import json
from datetime import datetime, timezone

from flask import current_app
from google.genai import types
from sqlalchemy import select

from app.agent.device_info import device_context
from app.agent.system_prompt import SYSTEM_PROMPT
from app.extensions import db
from app.models import AiNote, ConversationSummary, Message, UserProfile

# Quantas anotações recentes injetar no contexto
_MAX_NOTES = 20


def build_system_instruction(user_id: int) -> str:
    """Monta o system_instruction: prompt fixo + perfil + notas + resumo + dinâmico."""
    parts = [SYSTEM_PROMPT]

    prof = db.session.get(UserProfile, user_id)
    if prof and prof.profile:
        parts.append(
            "## Perfil do usuário (quem ele é, gostos, metas, rotina)\n"
            + json.dumps(prof.profile, ensure_ascii=False, indent=2)
        )

    notes = db.session.scalars(
        select(AiNote)
        .where(AiNote.user_id == user_id)
        .order_by(AiNote.created_at.desc())
        .limit(_MAX_NOTES)
    ).all()
    if notes:
        lines = []
        for n in reversed(notes):  # cronológico
            cat = f"[{n.category}] " if n.category else ""
            lines.append(f"- {cat}{n.content}")
        parts.append("## Suas anotações sobre o usuário\n" + "\n".join(lines))

    summary = db.session.get(ConversationSummary, user_id)
    if summary and summary.summary:
        parts.append(
            "## Resumo das conversas anteriores\n" + summary.summary
        )

    # Contexto dinâmico: nome e data/hora atual
    nome = None
    if prof and prof.profile:
        nome = prof.profile.get("nome_preferido")
    agora = datetime.now(timezone.utc).astimezone()
    dyn = [
        f"Data e hora atual: {agora.strftime('%A, %d/%m/%Y %H:%M')} "
        f"(horário local, fuso {agora.strftime('%z')}).",
        "Ao criar lembretes, informe `due_at` em ISO 8601 no horário local "
        f"(ex.: {agora.strftime('%Y-%m-%dT%H:%M:%S')}).",
    ]
    if nome:
        dyn.append(f"Nome do usuário: {nome}")
    parts.append("## Contexto do momento\n" + "\n".join(dyn))

    # Dispositivo onde a Helena roda (útil p/ tools que controlam o computador)
    parts.append("## Dispositivo onde você está rodando\n" + device_context())

    # Comandos e listas salvos que a Helena pode executar por nome
    autos = _saved_automations(user_id)
    if autos:
        parts.append(autos)

    return "\n\n".join(parts)


def _saved_automations(user_id: int) -> str | None:
    """Lista os comandos e rotinas salvos (nome — descrição), com teto."""
    from app.models import Routine, SavedCommand

    lines = []
    cmds = (
        db.session.query(SavedCommand)
        .filter_by(user_id=user_id)
        .order_by(SavedCommand.updated_at.desc())
        .limit(_MAX_NOTES)
        .all()
    )
    if cmds:
        lines.append("### Comandos salvos (execute com executar_comando)")
        for c in cmds:
            lines.append(f"- {c.name}: {c.description or '(sem descrição)'}")
    routines = (
        db.session.query(Routine)
        .filter_by(user_id=user_id)
        .order_by(Routine.updated_at.desc())
        .limit(_MAX_NOTES)
        .all()
    )
    if routines:
        lines.append("### Listas/rotinas salvas (execute com executar_lista)")
        for r in routines:
            lines.append(f"- {r.name}: {r.description or '(sem descrição)'}")
    if not lines:
        return None
    return "## Automações salvas do usuário\n" + "\n".join(lines)


def build_history(user_id: int) -> list[types.Content]:
    """Rabo não-resumido (cronológico) como histórico do Gemini.

    Devolve as mensagens ainda NÃO cobertas pelo `conversation_summary`
    (`id > last_summarized_msg_id`), garantindo que resumo + histórico
    particionem toda a conversa sem buraco (CLAUDE.md §5). O resumo rolante
    mantém esse rabo entre ~CHAT_WINDOW e 2×CHAT_WINDOW mensagens. Um teto de
    segurança evita crescimento descontrolado se o resumo falhar em série.
    A mensagem atual do usuário já foi persistida — é a última daqui.
    """
    window = current_app.config["CHAT_WINDOW"]
    summary = db.session.get(ConversationSummary, user_id)
    last_id = summary.last_summarized_msg_id if summary else None

    stmt = select(Message).where(Message.user_id == user_id)
    if last_id is not None:
        stmt = stmt.where(Message.id > last_id)
    # teto de segurança: no pior caso (resumo travado) limita os tokens
    stmt = stmt.order_by(Message.id.desc()).limit(4 * window)
    rows = db.session.scalars(stmt).all()
    rows.reverse()

    contents: list[types.Content] = []
    for idx, m in enumerate(rows):
        is_current = idx == len(rows) - 1
        parts = _message_parts(user_id, m, is_current)
        if not parts:
            continue
        role = "model" if m.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=parts))
    return contents


def _message_parts(user_id: int, m: Message, is_current: bool) -> list[types.Part]:
    """Partes de uma mensagem. Só a mensagem ATUAL manda bytes de imagem/pdf;
    histórico e áudio usam texto (transcript/descrição) — CLAUDE.md §3/§7."""
    from app.media import storage  # import tardio evita ciclo

    parts: list[types.Part] = []
    if m.content:
        text = m.content
        # a saída de shell entra como role=tool (→ 'user' p/ o modelo); enquadra
        # para a Helena saber que foi ELA que rodou, não o usuário.
        if m.role == "tool" and m.tool_name == "shell_output":
            text = "[Saída do comando que VOCÊ executou na máquina, a pedido do usuário]\n" + text
        parts.append(types.Part.from_text(text=text))

    if not m.media_url:
        return parts

    meta = m.media_meta or {}
    mt = m.media_type

    if mt == "audio":
        # nunca reenvia áudio; usa a transcrição feita no ingest
        transcript = meta.get("transcript")
        parts.append(
            types.Part.from_text(
                text=f"[áudio do usuário, transcrito]: {transcript or '(sem transcrição)'}"
            )
        )
    elif mt in ("image", "pdf") and is_current:
        path = storage.resolve(user_id, m.media_url)
        if path is not None:
            mime = meta.get("mime") or storage.classify(path.suffix)[1]
            parts.append(
                types.Part.from_bytes(data=path.read_bytes(), mime_type=mime)
            )
        else:
            parts.append(types.Part.from_text(text="[mídia indisponível]"))
    elif mt == "image":
        # description (imagem recebida) ou prompt (imagem gerada pela Helena)
        desc = meta.get("description") or meta.get("prompt")
        parts.append(
            types.Part.from_text(text=f"[imagem enviada: {desc or 'sem descrição'}]")
        )
    else:
        # pdf histórico, documento, planilha: placeholder textual
        name = meta.get("original_name") or mt or "arquivo"
        parts.append(types.Part.from_text(text=f"[{mt or 'documento'} enviado: {name}]"))

    return parts
