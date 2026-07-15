"""Tools do agente (function calling). Fase 2: nota e perfil.

Cada tool tem um schema (declarado ao Gemini) e um handler no backend.
Os handlers recebem `user_id` do backend — nunca do modelo — garantindo escopo.
Reminders, jobs e mídia entram nas fases seguintes.
"""
from google.genai import types
from sqlalchemy import select

from app.agent.automations_tools import (
    AUTOMATION_EXEC_DECLS, AUTOMATION_MANAGE_DECLS, AUTOMATION_HANDLERS,
)
from app.agent.desktop_tools import (
    DESKTOP_INPUT_DECLS, DESKTOP_VIEW_DECLS, DESKTOP_HANDLERS,
)
from app.agent.federation_tools import FEDERATION_INITIATE_DECLS, FEDERATION_INITIATE_HANDLERS
from app.agent.sandbox import EXECUTAR_CODIGO_DECL, executar_codigo
from app.agent.shell_tool import EXECUTAR_SHELL_DECL, executar_shell, shell_level
from app.extensions import db, write_lock
from app.models import AiNote, Peer, UserProfile

# --------------------------------------------------------------------------- #
# Schemas declarados ao Gemini
# --------------------------------------------------------------------------- #

_CHAT_BASE_DECLS = [
        types.FunctionDeclaration(
            name="create_note",
            description=(
                "Registra uma anotação duradoura sobre o usuário (um fato, uma "
                "preferência, um contexto ou uma pendência importante). Use quando "
                "aprender algo relevante que você não quer esquecer."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "content": types.Schema(
                        type=types.Type.STRING,
                        description="O conteúdo da anotação, em texto claro.",
                    ),
                    "category": types.Schema(
                        type=types.Type.STRING,
                        description="Categoria da anotação.",
                        enum=["contexto", "fato", "preferencia", "pendencia"],
                    ),
                    "tags": types.Schema(
                        type=types.Type.ARRAY,
                        description="Tags curtas opcionais para busca futura.",
                        items=types.Schema(type=types.Type.STRING),
                    ),
                },
                required=["content"],
            ),
        ),
        types.FunctionDeclaration(
            name="update_user_profile",
            description=(
                "Atualiza o perfil do usuário (gostos, rotina, metas, "
                "estilo_comunicacao, nome_preferido, referencias_que_curte). "
                "Passe apenas os campos a alterar — eles são mesclados ao perfil "
                "existente, sem apagar o resto."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "patch": types.Schema(
                        type=types.Type.OBJECT,
                        description=(
                            "Objeto parcial do perfil a mesclar. Ex.: "
                            '{"gostos": {"animes": ["Naruto"]}, '
                            '"metas": [{"meta": "treinar", "importancia": "alta"}]}'
                        ),
                    ),
                },
                required=["patch"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_image",
            description=(
                "Gera uma imagem a partir de uma descrição e a envia na conversa "
                "como uma mensagem de imagem. Use quando o usuário pedir uma "
                "imagem/desenho/arte ou quando ilustrar ajudar."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "prompt": types.Schema(
                        type=types.Type.STRING,
                        description="Descrição detalhada da imagem a gerar.",
                    ),
                },
                required=["prompt"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_audio",
            description=(
                "Fala em voz alta: converte um texto em áudio (voz da Helena) e "
                "envia como mensagem de áudio estilo WhatsApp. Use quando o usuário "
                "mandar áudio, pedir para você 'falar', ou quando fizer sentido."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "text": types.Schema(
                        type=types.Type.STRING,
                        description="O texto a ser falado em voz alta, em português.",
                    ),
                },
                required=["text"],
            ),
        ),
        types.FunctionDeclaration(
            name="generate_document",
            description=(
                "Gera um documento (pdf, docx, xlsx ou txt) e o envia na conversa. "
                "Use para relatórios, planos, planilhas ou textos que o usuário "
                "queira como arquivo."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "format": types.Schema(
                        type=types.Type.STRING,
                        description="Formato do arquivo.",
                        enum=["pdf", "docx", "xlsx", "txt"],
                    ),
                    "title": types.Schema(
                        type=types.Type.STRING, description="Título do documento."
                    ),
                    "content": types.Schema(
                        type=types.Type.STRING,
                        description="Conteúdo em texto (parágrafos separados por \\n).",
                    ),
                    "rows": types.Schema(
                        type=types.Type.ARRAY,
                        description="Para xlsx: linhas da planilha (lista de listas).",
                        items=types.Schema(
                            type=types.Type.ARRAY,
                            items=types.Schema(type=types.Type.STRING),
                        ),
                    ),
                },
                required=["format", "title"],
            ),
        ),
        types.FunctionDeclaration(
            name="create_reminder",
            description=(
                "Cria um lembrete na agenda. Use `agenda` (avisa 1 semana, 1 dia e "
                "6h antes) para compromissos com data marcada; `simple` (1 aviso) "
                "para lembretes pontuais. Para lembretes que se REPETEM (todo dia, "
                "toda semana, todo mês, todo ano), passe `recurrence` — o due_at é a "
                "primeira ocorrência. Você pode criar por conta própria quando "
                "inferir que o usuário quer trabalhar numa meta (origin='ai')."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING, description="Título do lembrete."),
                    "due_at": types.Schema(
                        type=types.Type.STRING,
                        description="Quando o evento acontece (1ª ocorrência, se recorrente), ISO 8601 local.",
                    ),
                    "description": types.Schema(type=types.Type.STRING, description="Detalhes (opcional)."),
                    "kind": types.Schema(
                        type=types.Type.STRING, enum=["agenda", "simple"],
                        description="agenda = 3 avisos; simple = 1 aviso. (recorrente é sempre simple.)",
                    ),
                    "recurrence": types.Schema(
                        type=types.Type.STRING, enum=["daily", "weekly", "monthly", "yearly"],
                        description="Repetição: daily=todo dia, weekly=toda semana, monthly=todo mês, yearly=todo ano. Omita para lembrete único.",
                    ),
                    "notify_at": types.Schema(
                        type=types.Type.STRING,
                        description="Para simple: quando avisar (ISO 8601 local). Default = due_at.",
                    ),
                    "origin": types.Schema(
                        type=types.Type.STRING, enum=["user", "ai"],
                        description="'user' se o usuário pediu; 'ai' se foi iniciativa sua.",
                    ),
                },
                required=["title", "due_at"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_agenda",
            description="Lista os lembretes do usuário (ordenados por data).",
            parameters=types.Schema(type=types.Type.OBJECT, properties={}),
        ),
        types.FunctionDeclaration(
            name="update_reminder",
            description="Atualiza um lembrete existente (título, descrição, data ou recorrência).",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reminder_id": types.Schema(type=types.Type.INTEGER, description="Id do lembrete."),
                    "title": types.Schema(type=types.Type.STRING),
                    "description": types.Schema(type=types.Type.STRING),
                    "due_at": types.Schema(type=types.Type.STRING, description="Nova data, ISO 8601 local."),
                    "recurrence": types.Schema(
                        type=types.Type.STRING, enum=["daily", "weekly", "monthly", "yearly", "none"],
                        description="Muda a repetição; 'none' torna o lembrete único (não-recorrente).",
                    ),
                },
                required=["reminder_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="delete_reminder",
            description="Remove um lembrete da agenda.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "reminder_id": types.Schema(type=types.Type.INTEGER, description="Id do lembrete."),
                },
                required=["reminder_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="run_background_job",
            description=(
                "Dispara um trabalho LONGO em segundo plano (pesquisa a fundo ou "
                "plano detalhado) e responde na hora sem travar o chat — o "
                "resultado chega depois como uma nova mensagem. LAPIDE antes: faça "
                "perguntas de refino para entender bem o que o usuário quer, e só "
                "então dispare. Não use para coisas rápidas nem para gerar "
                "imagem/áudio/documento (que já têm tools próprias)."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "type": types.Schema(
                        type=types.Type.STRING, enum=["research", "plan"],
                        description="research = pesquisa na web; plan = plano detalhado.",
                    ),
                    "payload": types.Schema(
                        type=types.Type.OBJECT,
                        description=(
                            'Parâmetros. Inclua "title" (rótulo curto) e "query" '
                            "(a tarefa já refinada, detalhada)."
                        ),
                    ),
                },
                required=["type", "payload"],
            ),
        ),
    ]

INICIAR_TAREFA_COMPUTADOR_DECL = types.FunctionDeclaration(
    name="iniciar_tarefa_computador",
    description=(
        "Dispara em SEGUNDO PLANO uma tarefa que precisa NAVEGAR/CLICAR/DIGITAR "
        "no computador do usuário (ex.: enviar currículo num site, buscar/comparar "
        "produtos em lojas diferentes, responder um e-mail) — não trava o chat, "
        "você avisa quando terminar. LAPIDE antes: confirme com o usuário o "
        "objetivo exato e os dados necessários (o que preencher, pra quem, "
        "critérios de busca) — só dispare quando tiver certeza do que fazer. Não "
        "use para coisas rápidas que dá pra fazer aqui mesmo no chat, nem para "
        "pesquisa textual (isso é `run_background_job` type=research)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title": types.Schema(type=types.Type.STRING, description="Rótulo curto da tarefa."),
            "task": types.Schema(
                type=types.Type.STRING,
                description="O objetivo detalhado e já refinado (o que fazer, passo a passo se souber).",
            ),
        },
        required=["title", "task"],
    ),
)

# Tiers de tools por nível de permissão (menos tokens + o modelo não recebe o
# que não pode usar): BASE (todos) + PRINCIPAL (ver tela/shell/executar) + FULL (mouse/teclado).
_BASE_DECLS = _CHAT_BASE_DECLS + AUTOMATION_MANAGE_DECLS + [EXECUTAR_CODIGO_DECL]
_PRINCIPAL_DECLS = [EXECUTAR_SHELL_DECL, *DESKTOP_VIEW_DECLS, *AUTOMATION_EXEC_DECLS]
_FULL_DECLS = [*DESKTOP_INPUT_DECLS, INICIAR_TAREFA_COMPUTADOR_DECL]

# conjunto completo (default / retrocompat p/ quem não passa user)
TOOL_DECLARATIONS = types.Tool(
    function_declarations=_BASE_DECLS + _PRINCIPAL_DECLS + _FULL_DECLS + FEDERATION_INITIATE_DECLS
)


def build_tool_declarations(user_id: int) -> types.Tool:
    """Declarações de tools do CHAT filtradas pelo nível do usuário."""
    decls = list(_BASE_DECLS)
    level = shell_level(user_id)
    if level in ("principal", "full"):
        decls += _PRINCIPAL_DECLS
    if level == "full":
        decls += _FULL_DECLS
    # Fase 3: só expõe as tools de federação se o usuário tiver ao menos um
    # peer pareado — evita poluir o prompt/tentar o modelo a chamar com
    # peer_id inventado quando não há nada com quem falar. O gate de
    # confiança/consentimento de verdade (ai_can_initiate + trust_level)
    # acontece em runtime no handler, não aqui.
    if db.session.query(Peer).filter_by(user_id=user_id).first() is not None:
        decls += FEDERATION_INITIATE_DECLS
    return types.Tool(function_declarations=decls)


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #

def _deep_merge(base: dict, patch: dict) -> dict:
    """Mescla `patch` em `base` recursivamente. Dicts fundem; listas/escalares
    substituem. Devolve novo dict (não muta `base` in place na raiz)."""
    out = dict(base)
    for key, value in patch.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _create_note(user_id: int, args: dict) -> dict:
    content = (args.get("content") or "").strip()
    if not content:
        return {"ok": False, "error": "content vazio"}
    category = args.get("category")
    tags = args.get("tags") or []
    with write_lock:
        note = AiNote(
            user_id=user_id, content=content, category=category, tags=tags
        )
        db.session.add(note)
        db.session.commit()
        note_id = note.id
    return {"ok": True, "note_id": note_id}


def _update_user_profile(user_id: int, args: dict) -> dict:
    patch = args.get("patch")
    if not isinstance(patch, dict) or not patch:
        return {"ok": False, "error": "patch precisa ser um objeto não-vazio"}
    with write_lock:
        prof = db.session.get(UserProfile, user_id)
        if prof is None:
            prof = UserProfile(user_id=user_id, profile={})
            db.session.add(prof)
        prof.profile = _deep_merge(prof.profile or {}, patch)
        db.session.commit()
        merged = prof.profile
    return {"ok": True, "profile": merged}


def _generation_handlers() -> dict:
    # import tardio: generate.py importa storage/models; evita ciclo no import
    from app.agent import generate
    from app.agent import agenda_tools as at
    from app.agent import job_tools

    return {
        "generate_image": generate.generate_image,
        "generate_audio": generate.generate_audio,
        "generate_document": generate.generate_document,
        "create_reminder": at.create_reminder,
        "list_agenda": at.list_agenda,
        "update_reminder": at.update_reminder,
        "delete_reminder": at.delete_reminder,
        "run_background_job": job_tools.run_background_job,
        "iniciar_tarefa_computador": _iniciar_tarefa_computador,
    }


def _iniciar_tarefa_computador(user_id: int, args: dict) -> dict:
    from app.agent import job_tools

    return job_tools.run_background_job(
        user_id,
        {
            "type": "desktop_task",
            "payload": {"title": args.get("title"), "task": args.get("task")},
        },
    )


_HANDLERS = {
    "create_note": _create_note,
    "update_user_profile": _update_user_profile,
    "executar_shell": executar_shell,
    "executar_codigo": executar_codigo,
    **DESKTOP_HANDLERS,
    **AUTOMATION_HANDLERS,
    **FEDERATION_INITIATE_HANDLERS,
}


def execute_tool(name: str, args: dict, user_id: int) -> dict:
    """Despacha uma tool call para seu handler. Retorna dict serializável."""
    handler = _HANDLERS.get(name) or _generation_handlers().get(name)
    if handler is None:
        return {"ok": False, "error": f"tool desconhecida: {name}"}
    try:
        return handler(user_id, args or {})
    except Exception as exc:  # noqa: BLE001 — devolve erro ao modelo, não derruba
        db.session.rollback()
        return {"ok": False, "error": str(exc)}
