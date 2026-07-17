"""Cliente Ollama + loop de tool-calling — equivalente LOCAL de gemini.py.

Mesmo contrato de `app.agent.gemini.run_agent`/`generate_text` (mesma
assinatura, mesmo retorno), para ser um drop-in via `app.agent.llm`. Fala com
a API REST do Ollama (`POST /api/chat`), sem streaming (paridade com o uso
não-streaming do Gemini hoje).

Diferenças importantes em relação ao Gemini:
- nem todo modelo Ollama suporta tool-calling — um modelo sem essa capability
  devolve HTTP 400 do próprio Ollama (falha REAL, não silenciosa); por isso
  o catálogo em `local_models.py` só inclui modelos com suporte confirmado;
- geração de imagem/áudio (TTS) não existem aqui — essas tools são filtradas
  antes de chegar em `tool_declarations` (ver `app.agent.tools.build_tool_declarations`);
- visão (screenshot) é melhor esforço: só funciona se o modelo escolhido for
  multimodal E suportar tools ao mesmo tempo — combinação rara. Se o modelo
  não suportar, a imagem é ignorada pelo Ollama sem travar o loop.
"""
import base64
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable

import requests
from flask import current_app
from google.genai import types

_SCREENSHOT_MARKER = "[Captura de tela atual:]"
_MAX_EMPTY_RETRIES = 3

_ollama_proc = None  # subprocess.Popen do 'ollama serve' que NÓS subimos, se algum


class OllamaError(Exception):
    pass


def _host() -> str:
    return (current_app.config.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")


def _request_timeout() -> int:
    return current_app.config.get("OLLAMA_REQUEST_TIMEOUT_SECONDS", 300)


def reachable(timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"{_host()}/api/version", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _post_chat(payload: dict) -> dict:
    try:
        r = requests.post(f"{_host()}/api/chat", json=payload, timeout=_request_timeout())
    except requests.RequestException as exc:
        raise OllamaError(f"não consegui falar com o Ollama em {_host()}: {exc}")
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("error", "")
        except ValueError:
            detail = r.text[:200]
        raise OllamaError(detail or f"HTTP {r.status_code}")
    return r.json()


# --------------------------------------------------------------------------- #
# conversão: schema/tools/histórico do formato Gemini (fonte única de
# verdade em app/agent/tools.py) pro formato OpenAI-style que o Ollama espera
# --------------------------------------------------------------------------- #

_TYPE_MAP = {
    types.Type.STRING: "string",
    types.Type.INTEGER: "integer",
    types.Type.NUMBER: "number",
    types.Type.BOOLEAN: "boolean",
    types.Type.ARRAY: "array",
    types.Type.OBJECT: "object",
}


def _schema_to_json(schema) -> dict:
    if schema is None:
        return {"type": "object", "properties": {}}
    out: dict = {"type": _TYPE_MAP.get(schema.type, "string")}
    if schema.description:
        out["description"] = schema.description
    if schema.enum:
        out["enum"] = list(schema.enum)
    if schema.properties:
        out["properties"] = {k: _schema_to_json(v) for k, v in schema.properties.items()}
    if schema.required:
        out["required"] = list(schema.required)
    if schema.items:
        out["items"] = _schema_to_json(schema.items)
    return out


def tool_declarations_to_ollama(tool: types.Tool | None) -> list[dict] | None:
    if tool is None or not tool.function_declarations:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": fd.name,
                "description": fd.description or "",
                "parameters": _schema_to_json(fd.parameters),
            },
        }
        for fd in tool.function_declarations
    ]


def _content_to_message(content: types.Content) -> dict | None:
    role = {"model": "assistant", "user": "user", "tool": "tool"}.get(content.role, "user")
    texts = []
    images = []
    for part in content.parts or []:
        if part.text:
            texts.append(part.text)
        elif part.inline_data is not None and (part.inline_data.mime_type or "").startswith("image/"):
            images.append(base64.b64encode(part.inline_data.data).decode("ascii"))
        # outros tipos (pdf, function_response bruto) não têm equivalente
        # simples no Ollama — melhor esforço, ficam de fora silenciosamente
    if not texts and not images:
        return None
    msg = {"role": role, "content": "\n".join(texts)}
    if images:
        msg["images"] = images
    return msg


def history_to_ollama(contents: list[types.Content]) -> list[dict]:
    out = []
    for c in contents:
        m = _content_to_message(c)
        if m is not None:
            out.append(m)
    return out


# --------------------------------------------------------------------------- #
# geração de texto simples (sem tools) — resumo, memória, corpo de notificação
# --------------------------------------------------------------------------- #

def generate_text(system_instruction: str, contents, *, api_key: str = "", model: str, json_mode: bool = False) -> str:
    messages = [{"role": "system", "content": system_instruction}]
    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
    else:
        messages.extend(history_to_ollama(contents))
    payload = {"model": model, "messages": messages, "stream": False}
    if json_mode:
        payload["format"] = "json"
    resp = _post_chat(payload)
    return (resp.get("message", {}).get("content") or "").strip()


# --------------------------------------------------------------------------- #
# loop de tool-calling
# --------------------------------------------------------------------------- #

def _wrapup_text(model: str, messages: list[dict]) -> str:
    """Mesmo espírito de gemini.py::_wrapup_text — fecho em texto quando o
    loop termina de forma anormal, pra não perder o progresso já feito."""
    ask = {"role": "user", "content": (
        "Pare — não chame mais nenhuma ferramenta. Resuma em texto corrido, em "
        "português, exatamente o que você já conseguiu fazer até aqui e o que "
        "ficou faltando ou travou (diga o que travou, se algo travou)."
    )}
    try:
        resp = _post_chat({"model": model, "messages": [*messages, ask], "stream": False})
        return (resp.get("message", {}).get("content") or "").strip()
    except OllamaError:
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
    """Equivalente local de `gemini.run_agent` — mesmo contrato (ver lá pros
    comentários detalhados de cada parâmetro). `api_key` é aceito só por
    compatibilidade de assinatura (Ollama não usa chave)."""
    from app.agent import context as ctx
    from app.agent.tools import build_tool_declarations, execute_tool

    if not model:
        return (
            "Nenhum modelo local configurado — rode 'helena models use' ou "
            "'helena setup' pra escolher um.",
            False,
        )

    if system_instruction is None:
        system_instruction = ctx.build_system_instruction(user_id)
    contents = initial_contents if initial_contents is not None else ctx.build_history(user_id)
    dispatch = dispatch or execute_tool
    if tool_declarations is None:
        tool_declarations = build_tool_declarations(user_id, provider="ollama")

    tools_payload = tool_declarations_to_ollama(tool_declarations)
    messages = [{"role": "system", "content": system_instruction}, *history_to_ollama(contents)]

    final_text = ""
    tool_executed = False
    last_sig = None
    repeat_count = 0
    empty_retries = 0
    needs_wrapup = False

    for _ in range(max_iters):
        if deadline is not None and time.monotonic() > deadline:
            needs_wrapup = True
            break

        try:
            resp = _post_chat({
                "model": model,
                "messages": messages,
                "tools": tools_payload,
                "stream": False,
                "options": {"temperature": current_app.config["GEMINI_TEMPERATURE"]},
            })
        except OllamaError as exc:
            final_text = f"Hmm, não consegui falar com o modelo local agora ({exc})."
            break

        msg = resp.get("message") or {}
        calls = msg.get("tool_calls") or []
        step_text = (msg.get("content") or "").strip()

        if not calls and not step_text:
            empty_retries += 1
            if empty_retries <= _MAX_EMPTY_RETRIES:
                continue
            needs_wrapup = True
            break
        empty_retries = 0

        if not calls:
            final_text = step_text
            break

        if step_text and on_progress is not None:
            on_progress(step_text)

        sig = tuple(
            (c.get("function", {}).get("name"), str(c.get("function", {}).get("arguments") or {}))
            for c in calls
        )
        if sig == last_sig:
            repeat_count += 1
            if repeat_count >= stuck_repeat_limit:
                needs_wrapup = True
                break
        else:
            repeat_count = 0
        last_sig = sig

        tool_executed = True
        messages.append({"role": "assistant", "content": step_text, "tool_calls": calls})

        # resultados na MESMA ORDEM das calls — sem 'id' pra correlacionar,
        # a maioria dos templates do Ollama associa resultado↔chamada por
        # ordem (nunca despachar/anexar fora de ordem ou concorrentemente)
        pending = False
        images: list[str] = []
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name")
            args = fn.get("arguments") or {}
            result = dispatch(name, args, user_id)
            if isinstance(result, dict):
                if result.get("pending_approval"):
                    pending = True
                img = result.pop("__inject_image__", None)
                result.pop("__inject_mime__", None)
                if img is not None:
                    images.append(base64.b64encode(img).decode("ascii"))
            messages.append({"role": "tool", "content": json.dumps(result, ensure_ascii=False)})

        if images:
            # mantém só o screenshot MAIS RECENTE (mesmo motivo do gemini.py:
            # empilhar prints full-res estoura o tamanho da request)
            messages = [
                m for m in messages
                if not (m.get("role") == "user" and m.get("content") == _SCREENSHOT_MARKER)
            ]
            messages.append({"role": "user", "content": _SCREENSHOT_MARKER, "images": images})

        if pending:
            break
    else:
        needs_wrapup = True

    if needs_wrapup and not final_text:
        final_text = (_wrapup_text(model, messages) if tool_executed else "") or (
            "Fiz o que pude aqui, mas me embananei um pouco. Pode repetir do seu jeito?"
        )

    return final_text or "", tool_executed


# --------------------------------------------------------------------------- #
# ciclo de vida do daemon `ollama serve` (chamado do run.py)
# --------------------------------------------------------------------------- #

def ensure_running(app) -> None:
    """Sobe `ollama serve` junto do processo da Helena, se aplicável.

    Cobre tanto `helena start` (pidfile — o filho herda o MESMO grupo de
    processo do servidor, então `_kill_tree`/`os.killpg` mata os dois juntos)
    quanto o serviço systemd (`KillMode=control-group`, default do systemd,
    mata TODO o cgroup da unit ao parar — incluindo processos filhos criados
    depois do start) com a MESMA implementação, sem precisar de uma segunda
    unit nem de pidfile próprio. Não-op se o provider não é 'ollama', se
    `OLLAMA_MANAGED=0`, ou se já tem algo respondendo em OLLAMA_HOST (nunca
    mexe numa instância que já existia antes)."""
    global _ollama_proc

    cfg = app.config
    if cfg.get("LLM_PROVIDER") != "ollama" or not cfg.get("OLLAMA_MANAGED", True):
        return

    host = cfg.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    with app.app_context():
        if reachable():
            app.logger.info("Ollama já está respondendo em %s — nada a subir.", host)
            return

    if not shutil.which("ollama"):
        app.logger.warning(
            "LLM_PROVIDER=ollama mas o binário 'ollama' não foi encontrado no PATH "
            "(rode 'helena setup' de novo ou instale manualmente: https://ollama.com/download)."
        )
        return

    try:
        _ollama_proc = subprocess.Popen(
            ["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        app.logger.warning("não consegui subir 'ollama serve': %s", exc)
        return

    def _watch(proc):
        time.sleep(3)
        with app.app_context():
            ok = reachable()
        if not ok:
            # perdeu a corrida pra outra instância (systemd system-level do
            # Ollama, ou um 'ollama serve' já rodando por fora) — não é erro,
            # só espera sair sozinho pra não deixar zumbi.
            app.logger.debug(
                "nossa tentativa de subir o ollama não conseguiu bind — "
                "provável instância já existente na porta."
            )
        proc.wait()

    threading.Thread(target=_watch, name="ollama-watch", args=(_ollama_proc,), daemon=True).start()
