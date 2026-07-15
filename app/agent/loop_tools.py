"""Tools do loop de agente autônomo (background jobs iterativos).

Conjunto SEPARADO das tools do chat. Hoje só `pesquisar` — uma busca real na
web com grounding. Ela é uma function-tool (e não `google_search` direto na
config) porque o Gemini não permite combinar `google_search` com declarações de
função na MESMA chamada: o loop precisa de function-calling, então a busca roda
numa chamada grounded interna, isolada, invocada por esta tool.

O conjunto é plugável de propósito — execução de código (sandbox) entra aqui
depois, como decisão de segurança à parte.
"""
from flask import current_app
from google.genai import types

from app.agent.sandbox import EXECUTAR_CODIGO_DECL, executar_codigo

LOOP_TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="pesquisar",
            description=(
                "Pesquisa na web (fontes atuais) sobre UM assunto/subtópico e "
                "devolve um resumo dos achados. Chame uma vez por subtópico. "
                "Antes de cada chamada, escreva uma frase curta de progresso para "
                "o usuário (ex.: 'já entendi sobre X, agora vou ver Y')."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "assunto": types.Schema(
                        type=types.Type.STRING,
                        description="O subtópico específico a pesquisar agora.",
                    ),
                },
                required=["assunto"],
            ),
        ),
        EXECUTAR_CODIGO_DECL,
    ]
)


def _pesquisar(user_id: int, args: dict) -> dict:
    assunto = (args.get("assunto") or "").strip()
    if not assunto:
        return {"ok": False, "error": "assunto vazio"}
    from app.agent.gemini import get_client  # import tardio (evita ciclo)

    cfg = current_app.config
    client = get_client(cfg["GEMINI_API_KEY"])
    resp = client.models.generate_content(
        model=cfg["GEMINI_MODEL"],
        contents=f"Pesquise e resuma o mais relevante e atual sobre: {assunto}",
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    achados = (resp.text or "").strip()
    if not achados:
        return {"ok": False, "assunto": assunto, "error": "nada encontrado"}
    return {"ok": True, "assunto": assunto, "achados": achados[:4000]}


_LOOP_HANDLERS = {"pesquisar": _pesquisar, "executar_codigo": executar_codigo}


def dispatch_loop_tool(name: str, args: dict, user_id: int) -> dict:
    """Despacha uma tool do loop. Mesma assinatura de execute_tool."""
    handler = _LOOP_HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"tool desconhecida no loop: {name}"}
    try:
        return handler(user_id, args or {})
    except Exception as exc:  # noqa: BLE001 — devolve erro ao modelo, não derruba
        return {"ok": False, "error": str(exc)}
