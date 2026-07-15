"""Tools da TAREFA de desktop autônoma em background (job type=desktop_task).

Reusa os MESMOS handlers do chat (`desktop_tools.DESKTOP_HANDLERS`) — o gate de
permissão (`_deny`) já embutido neles vale aqui também: um panic/rebaixamento no
meio da tarefa barra a próxima ação automaticamente, sem código extra aqui.

Diferença-chave em relação ao chat: depois de qualquer ação de INPUT bem-sucedida
(clicar/digitar/tecla/rolar/abrir_navegador), este dispatcher tira um print
SOZINHO e injeta no resultado (`__inject_image__`) — o modelo nunca decide por
conta própria se vai conferir o resultado; ele sempre vê antes do próximo passo.
Isso é o que resolve "ela tenta fazer sem ter certeza se deu certo": a verificação
não depende de o modelo lembrar de chamar capturar_tela.
"""
import time

from google.genai import types

from app.agent.desktop_tools import (
    DESKTOP_HANDLERS,
    DESKTOP_INPUT_DECLS,
    DESKTOP_VIEW_DECLS,
    _capturar_tela,
    _deny,
)

ABRIR_NAVEGADOR_DECL = types.FunctionDeclaration(
    name="abrir_navegador",
    description=(
        "Abre o navegador PADRÃO configurado pelo usuário (ou o único instalado), "
        "opcionalmente já numa URL. PREFIRA abrir direto na URL quando já souber o "
        "endereço, em vez de abrir em branco e navegar por clique — menos passos, "
        "menos custo."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "url": types.Schema(type=types.Type.STRING, description="URL opcional para abrir já."),
        },
    ),
)

# ações de INPUT: depois de rodarem OK, o dispatcher confere sozinho com um print
_AUTO_VERIFY_ACTIONS = {"clicar", "digitar", "tecla", "rolar", "abrir_navegador"}
# tempo pra página/animação assentar antes do print de conferência
_SETTLE_SECONDS = 0.8


def _abrir_navegador(user_id: int, args: dict) -> dict:
    err = _deny(user_id, need_full=True)
    if err:
        return {"ok": False, "error": err}
    from app.agent import browsers

    url = (args.get("url") or "").strip() or None
    try:
        label = browsers.open_browser_for_user(user_id, url)
    except browsers.BrowserError as exc:
        return {"ok": False, "error": str(exc)}
    from app import audit
    audit.record(user_id, "desktop", f"abrir_navegador {label}" + (f" {url}" if url else ""))
    return {"ok": True, "info": f"{label} aberto" + (f" em {url}" if url else "")}


_TASK_HANDLERS = {**DESKTOP_HANDLERS, "abrir_navegador": _abrir_navegador}

DESKTOP_TASK_TOOLS = types.Tool(
    function_declarations=[*DESKTOP_VIEW_DECLS, *DESKTOP_INPUT_DECLS, ABRIR_NAVEGADOR_DECL]
)


def dispatch_desktop_task_tool(name: str, args: dict, user_id: int) -> dict:
    """Despacha uma tool da tarefa de desktop. Mesma assinatura de execute_tool."""
    handler = _TASK_HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"tool desconhecida: {name}"}
    try:
        result = handler(user_id, args or {})
    except Exception as exc:  # noqa: BLE001 — devolve erro ao modelo, não derruba o job
        return {"ok": False, "error": str(exc)}

    if name in _AUTO_VERIFY_ACTIONS and isinstance(result, dict) and result.get("ok"):
        time.sleep(_SETTLE_SECONDS)
        shot = _capturar_tela(user_id, {})
        if shot.get("ok"):
            result["__inject_image__"] = shot.get("__inject_image__")
            result["__inject_mime__"] = shot.get("__inject_mime__")
            result["conferencia"] = "print automático após a ação — confira antes de seguir"
    return result
