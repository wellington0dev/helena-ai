"""Executores de background job por tipo (CLAUDE.md §7/§8).

- `research`: loop de agente AUTÔNOMO e iterativo — quebra o tema em subtópicos,
  pesquisa um a um (tool `pesquisar`, com grounding) e vai dando feedback de
  progresso; termina naturalmente escrevendo a entrega final.
- `plan`: geração única (um plano acionável).

Geração de imagem/áudio/documento continua síncrona (tools do chat) — não aqui.
"""
import time
from collections.abc import Callable

from flask import current_app
from google.genai import types

from app.agent import gemini
from app.agent.gemini import get_client
from app.agent.loop_tools import LOOP_TOOLS, dispatch_loop_tool
from app.models import Job

ProgressFn = Callable[[str], None]

_RESEARCH_LOOP_INSTRUCTION = (
    "Você é a Helena fazendo uma pesquisa a fundo para o usuário, de forma AUTÔNOMA "
    "e iterativa, em segundo plano. Quebre o tema em subtópicos e investigue um de "
    "cada vez chamando a ferramenta `pesquisar`. IMPORTANTE: para realmente descobrir "
    "algo você TEM que chamar `pesquisar` — só escrever que vai pesquisar não busca "
    "nada. Antes de cada busca, escreva UMA frase curta e casual de progresso para o "
    "usuário acompanhar (ex.: 'já entendi sobre X, agora vou ver Y'). Quando tiver "
    "coberto o suficiente, PARE de chamar ferramentas e escreva a ENTREGA final: um "
    "texto completo, organizado e em português, sintetizando tudo que encontrou, com "
    "títulos/tópicos quando ajudar."
)
_PLAN_INSTRUCTION = (
    "Você é a Helena montando um plano prático para o usuário. Entregue um plano "
    "acionável em português: etapas claras, ordem, e o que fazer em cada uma."
)


def _prompt(job: Job) -> str:
    p = job.payload or {}
    return (p.get("query") or p.get("task") or p.get("title") or "").strip()


def _kickoff(task: str, force_search: bool = False) -> list:
    text = f"Tarefa de pesquisa: {task}"
    if force_search:
        text += "\n\nComece AGORA chamando a ferramenta `pesquisar` no primeiro subtópico."
    return [types.Content(role="user", parts=[types.Part.from_text(text=text)])]


def _research(job: Job, on_progress: ProgressFn | None = None) -> str:
    cfg = current_app.config
    task = _prompt(job)

    def _run(force_search: bool):
        return gemini.run_agent(
            user_id=job.user_id,
            api_key=cfg["GEMINI_API_KEY"],
            model=cfg["GEMINI_MODEL"],
            max_iters=cfg["MAX_JOB_ITERATIONS"],
            system_instruction=_RESEARCH_LOOP_INSTRUCTION,
            initial_contents=_kickoff(task, force_search),
            tool_declarations=LOOP_TOOLS,
            dispatch=dispatch_loop_tool,
            on_progress=on_progress,
            deadline=time.monotonic() + cfg["JOB_TIMEOUT_SECONDS"],
        )

    text, searched = _run(force_search=False)
    # narrou sem pesquisar nada → força uma tentativa começando pela busca
    if not searched:
        text, _ = _run(force_search=True)
    return text


def _plan(job: Job, on_progress: ProgressFn | None = None) -> str:
    cfg = current_app.config
    client = get_client(cfg["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=cfg["GEMINI_MODEL"],
        contents=_prompt(job),
        config=types.GenerateContentConfig(system_instruction=_PLAN_INSTRUCTION),
    )
    return (resp.text or "").strip()


_EXECUTORS = {"research": _research, "plan": _plan}


def execute_job(job: Job, on_progress: ProgressFn | None = None) -> str:
    """Roda o job e devolve o texto da entrega. `on_progress(texto)` recebe o
    feedback contínuo do loop (ignorado por tipos não-iterativos)."""
    fn = _EXECUTORS.get(job.type)
    if fn is None:
        raise ValueError(f"tipo de job não suportado: {job.type}")
    text = fn(job, on_progress)
    if not text:
        raise RuntimeError("o modelo não retornou resultado")
    return text
