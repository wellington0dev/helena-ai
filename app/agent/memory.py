"""Memória de longo prazo: consolida notas antigas no perfil estruturado.

As `ai_notes` têm teto no contexto (~20 recentes); as antigas cairiam fora. Ao
passar de um limite, um LLM absorve os fatos DURÁVEIS das notas mais antigas no
`user_profile.profile` (que sempre entra no contexto) e essas notas são podadas.

Guardas contra perda de dados (é uma reescrita LLM da memória do usuário):
- NUNCA poda se a chamada falhar ou vier JSON vazio/ inválido;
- só aceita um perfil novo que MANTENHA todas as chaves de topo do perfil atual.
"""
import json

from flask import current_app
from sqlalchemy import select

from app.agent import llm
from app.extensions import db, write_lock
from app.models import AiNote, UserProfile

_INSTRUCTION = (
    "Você recebe o perfil JSON atual do usuário e uma lista de anotações. Devolva "
    "um JSON do perfil ATUALIZADO que absorve os fatos DURÁVEIS das anotações "
    "(gostos, rotina, metas, fatos, preferências), MANTENDO tudo que já existe no "
    "perfil (não remova chaves). Compacto, sem redundância. Responda só o JSON."
)


def maybe_consolidate(user_id: int) -> None:
    threshold = current_app.config["MEMORY_NOTES_THRESHOLD"]
    keep = current_app.config["MEMORY_NOTES_KEEP"]
    total = db.session.query(AiNote).filter_by(user_id=user_id).count()
    if total <= threshold:
        return

    old_notes = db.session.scalars(
        select(AiNote)
        .filter_by(user_id=user_id)
        .order_by(AiNote.created_at.asc())
        .limit(total - keep)
    ).all()
    if not old_notes:
        return

    prof = db.session.get(UserProfile, user_id)
    current = (prof.profile if prof else {}) or {}
    notes_text = "\n".join(f"- [{n.category}] {n.content}" for n in old_notes)

    try:
        prompt = (
            f"Perfil atual:\n{json.dumps(current, ensure_ascii=False)}\n\n"
            f"Anotações a absorver:\n{notes_text}"
        )
        text = llm.generate_text(_INSTRUCTION, prompt, json_mode=True)
        new_profile = json.loads(text or "")
    except Exception as exc:  # noqa: BLE001 — falha NUNCA poda (sem perda de dados)
        current_app.logger.warning("consolidação de memória falhou: %s", exc)
        return

    # guardas: precisa ser dict não-vazio e manter todas as chaves anteriores
    if not isinstance(new_profile, dict) or not new_profile:
        current_app.logger.warning("consolidação: JSON vazio/ inválido; abortando (nada podado)")
        return
    if not set(current.keys()) <= set(new_profile.keys()):
        current_app.logger.warning("consolidação perderia chaves do perfil; abortando")
        return

    with write_lock:
        if prof is None:
            prof = UserProfile(user_id=user_id, profile={})
            db.session.add(prof)
        prof.profile = new_profile
        for n in old_notes:
            db.session.delete(n)
        db.session.commit()
    current_app.logger.info(
        "memória consolidada: %s notas antigas → perfil (user %s)", len(old_notes), user_id
    )
