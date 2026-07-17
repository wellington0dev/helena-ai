"""Cliente Gemini + loop de tool-calling."""
import time
from collections.abc import Callable

from flask import current_app
from google import genai
from google.genai import types

from app.agent import context as ctx
from app.agent.tools import TOOL_DECLARATIONS, build_tool_declarations, execute_tool

_client: genai.Client | None = None

# Quantas respostas VAZIAS (candidato STOP sem partes: nem tool, nem texto)
# tolerar em sequência antes de desistir. O Gemini às vezes devolve um no-op
# degenerado (comum ao pedir áudio); reamostrar o mesmo passo costuma resolver.
_MAX_EMPTY_RETRIES = 3

# marcador do turno de imagem injetado (screenshot) — usado para manter só o
# print mais recente no contexto (evita empilhar imagens full-res num loop)
_SCREENSHOT_MARKER = "[Captura de tela atual:]"


def get_client(api_key: str) -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=api_key)
    return _client


def generate_text(system_instruction: str, contents, *, api_key: str, model: str, json_mode: bool = False) -> str:
    """Geração de texto solta (sem tools) — resumo, memória, corpo de
    notificação etc. `contents` aceita tanto uma string quanto uma lista de
    `types.Content` (multi-turno), igual ao SDK do Gemini já aceita nativamente."""
    client = get_client(api_key)
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        **({"response_mime_type": "application/json"} if json_mode else {}),
    )
    resp = client.models.generate_content(model=model, contents=contents, config=config)
    return (resp.text or "").strip()


def _is_screenshot_turn(content) -> bool:
    """True se `content` é um turno de screenshot injetado (para descartá-lo e
    manter só o mais recente)."""
    try:
        parts = content.parts or []
        return bool(parts) and getattr(parts[0], "text", None) == _SCREENSHOT_MARKER
    except AttributeError:
        return False


def _wrapup_text(client, model, contents, base_config) -> str:
    """Pede um fecho em TEXTO (sem tools) quando o loop termina de forma
    ANORMAL (empacado, timeout ou orçamento de iterações esgotado) — sem isso,
    uma tarefa longa que não termina "naturalmente" perde todo o progresso já
    feito (o job vira erro em vez de contar o que deu certo/o que travou).
    Best-effort: nunca levanta, um resumo vazio só faz cair no fallback genérico."""
    ask = types.Content(role="user", parts=[types.Part.from_text(text=(
        "Pare — não chame mais nenhuma ferramenta. Resuma em texto corrido, em "
        "português, exatamente o que você já conseguiu fazer até aqui e o que "
        "ficou faltando ou travou (diga o que travou, se algo travou)."
    ))])
    wrapup_config = types.GenerateContentConfig(
        system_instruction=base_config.system_instruction,
        temperature=base_config.temperature,
    )
    try:
        resp = client.models.generate_content(
            model=model, contents=[*contents, ask], config=wrapup_config,
        )
        return (resp.text or "").strip()
    except Exception:  # noqa: BLE001 — fecho é best-effort, não pode derrubar o loop
        return ""


def run_agent(
    user_id: int,
    api_key: str,
    model: str,
    max_iters: int,
    *,
    system_instruction: str | None = None,
    initial_contents: list | None = None,
    tool_declarations: types.Tool | None = None,
    dispatch: Callable[[str, dict, int], dict] | None = None,
    on_progress: Callable[[str], None] | None = None,
    deadline: float | None = None,
    stuck_repeat_limit: int = 1,
) -> tuple[str, bool]:
    """Roda o loop de tool-calling e devolve `(texto_final, alguma_tool_executou)`.

    Sem overrides, roda o turno de CHAT do usuário (contexto/tools do chat). Os
    overrides permitem reusá-lo como loop de agente autônomo em background:
    - `system_instruction`/`initial_contents`: objetivo e ponto de partida próprios;
    - `tool_declarations`/`dispatch`: conjunto de tools alternativo;
    - `on_progress(texto)`: recebe o texto que o modelo escreve JUNTO das tool
      calls em cada passo — é o "feedback contínuo" (ex.: "já entendi X, seguindo");
    - `deadline`: instante (time.monotonic) para parar por timeout;
    - `stuck_repeat_limit`: quantas chamadas IDÊNTICAS consecutivas tolerar antes
      de considerar "empacado" e encerrar. 1 (padrão) = encerra na 1ª repetição
      (bom pra pesquisa: repetir a mesma busca é sinal de loop). Tarefas de
      desktop legitimamente repetem (rolar 2x, tab entre campos) — usar mais.

    Termina naturalmente quando o modelo para de chamar tools (o texto final vira
    a entrega) — de propósito não há tool `finalizar` que o modelo poderia narrar
    sem chamar. O booleano distingue um turno que agiu de um no-op puro.
    """
    client = get_client(api_key)
    if system_instruction is None:
        system_instruction = ctx.build_system_instruction(user_id)
    contents = initial_contents if initial_contents is not None else ctx.build_history(user_id)
    dispatch = dispatch or execute_tool
    # chat (sem override): tools filtradas pelo nível de permissão do usuário
    if tool_declarations is None:
        tool_declarations = build_tool_declarations(user_id)

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[tool_declarations],
        # temperatura mais baixa = mais consistência em chamar as tools (menos
        # "narrar em vez de agir"). A personalidade vem do prompt, não do calor.
        temperature=current_app.config["GEMINI_TEMPERATURE"],
        # desliga a execução automática — despachamos nós mesmos (escopo + DB)
        automatic_function_calling=types.AutomaticFunctionCallingConfig(
            disable=True
        ),
    )

    final_text = ""
    tool_executed = False
    last_sig = None
    repeat_count = 0
    empty_retries = 0
    # True quando o loop termina de forma ANORMAL (não foi o modelo que decidiu
    # parar) — nesses casos tentamos um fecho em texto antes de desistir, pra
    # não perder o progresso de uma tarefa longa (ver _wrapup_text).
    needs_wrapup = False
    for _ in range(max_iters):
        if deadline is not None and time.monotonic() > deadline:
            needs_wrapup = True
            break

        response = client.models.generate_content(
            model=model, contents=contents, config=config
        )

        # resposta bloqueada/vazia (safety, sem candidatos) → fecha amigável
        if not response.candidates:
            final_text = (
                "Hmm, não consegui formular uma resposta pra isso agora. "
                "Tenta de outro jeito?"
            )
            break

        calls = response.function_calls or []
        step_text = (response.text or "").strip()

        # Resposta DEGENERADA: candidato STOP porém vazio (0 partes — nem tool,
        # nem texto). É um no-op transitório do Gemini (frequente ao pedir
        # áudio), não uma resposta final. Reamostra o MESMO passo algumas vezes
        # antes de desistir — costuma virar uma chamada de tool de verdade.
        if not calls and not step_text:
            empty_retries += 1
            if empty_retries <= _MAX_EMPTY_RETRIES:
                continue
            needs_wrapup = True
            break
        empty_retries = 0  # passo produtivo → zera a contagem de vazios

        if not calls:
            final_text = step_text
            break

        # texto que acompanha as tool calls = feedback de progresso
        if step_text and on_progress is not None:
            on_progress(step_text)

        # detector de "empacamento": mesma(s) chamada(s) repetida(s) em sequência
        # além do limite tolerado → encerra (evita loop infinito de tool idêntica)
        sig = tuple((c.name, str(dict(c.args or {}))) for c in calls)
        if sig == last_sig:
            repeat_count += 1
            if repeat_count >= stuck_repeat_limit:
                needs_wrapup = True
                break
        else:
            repeat_count = 0
        last_sig = sig

        tool_executed = True
        # registra o turno do modelo (as function calls) no histórico
        contents.append(response.candidates[0].content)

        # executa cada tool e devolve os resultados
        tool_parts = []
        pending = False
        images: list[tuple[str, bytes]] = []
        for call in calls:
            result = dispatch(call.name, dict(call.args or {}), user_id)
            if isinstance(result, dict):
                if result.get("pending_approval"):
                    pending = True
                # imagem a INJETAR (ex.: screenshot) — não cabe no function_response
                # (que é JSON); sai daqui e entra como turno de imagem separado.
                img = result.pop("__inject_image__", None)
                mime = result.pop("__inject_mime__", "image/png")
                if img is not None:
                    images.append((mime, img))
            tool_parts.append(
                types.Part.from_function_response(
                    name=call.name, response=result
                )
            )
        contents.append(types.Content(role="tool", parts=tool_parts))

        # injeta as imagens capturadas como um turno de "usuário" para o modelo
        # VER (padrão computer-use: function_response leva JSON, a imagem vem à
        # parte). É isso que permite a Helena enxergar a tela e agir.
        if images:
            # mantém só o screenshot MAIS RECENTE no contexto: descarta os turnos
            # de imagem anteriores (num loop ver→agir→ver, empilhar prints full-res
            # estoura o tamanho da request).
            contents = [c for c in contents if not _is_screenshot_turn(c)]
            parts = [types.Part.from_text(text=_SCREENSHOT_MARKER)]
            for mime, data in images:
                parts.append(types.Part.from_bytes(data=data, mime_type=mime))
            contents.append(types.Content(role="user", parts=parts))

        # uma tool pediu permissão ao usuário (ex.: executar_shell) → PARA o
        # turno aqui; a mensagem de pedido já foi persistida e o agente só
        # continua depois que o usuário decidir (via endpoint de decisão).
        if pending:
            break  # UI própria de aprovação — não é caso de fecho, fica sem texto
    else:
        needs_wrapup = True  # esgotou max_iters sem resposta natural do modelo

    if needs_wrapup and not final_text:
        final_text = (_wrapup_text(client, model, contents, config) if tool_executed else "") or (
            "Fiz o que pude aqui, mas me embananei um pouco. "
            "Pode repetir do seu jeito?"
        )

    return final_text or "", tool_executed
