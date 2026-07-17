"""Cliente Ollama (app/agent/ollama_client.py):
- conversão de tool schema (Gemini -> formato OpenAI-style do Ollama);
- conversão de histórico (types.Content -> mensagens do Ollama);
- run_agent fim-a-fim com requests.post monkeypatchado (sem rede real);
- HTTP não-2xx vira mensagem amigável, não exceção crua (modelo sem suporte
  a tools responde 400 — é o modo de falha real do Ollama, não silencioso)."""
from google.genai import types

from app.agent import ollama_client as oc


def _tool():
    return types.Tool(function_declarations=[
        types.FunctionDeclaration(
            name="criar_nota",
            description="Cria uma nota.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "texto": types.Schema(type=types.Type.STRING, description="conteúdo"),
                    "tags": types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
                },
                required=["texto"],
            ),
        )
    ])


def test_tool_declarations_to_ollama_formato_openai():
    out = oc.tool_declarations_to_ollama(_tool())
    assert out == [{
        "type": "function",
        "function": {
            "name": "criar_nota",
            "description": "Cria uma nota.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string", "description": "conteúdo"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["texto"],
            },
        },
    }]


def test_tool_declarations_to_ollama_none_sem_tools():
    assert oc.tool_declarations_to_ollama(None) is None
    assert oc.tool_declarations_to_ollama(types.Tool(function_declarations=[])) is None


def test_history_to_ollama_texto():
    contents = [
        types.Content(role="user", parts=[types.Part.from_text(text="oi")]),
        types.Content(role="model", parts=[types.Part.from_text(text="olá!")]),
    ]
    out = oc.history_to_ollama(contents)
    assert out == [
        {"role": "user", "content": "oi"},
        {"role": "assistant", "content": "olá!"},
    ]


def test_history_to_ollama_imagem_vira_base64():
    contents = [
        types.Content(role="user", parts=[types.Part.from_bytes(data=b"\x89PNGabc", mime_type="image/png")]),
    ]
    out = oc.history_to_ollama(contents)
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert "images" in out[0]
    import base64
    assert base64.b64decode(out[0]["images"][0]) == b"\x89PNGabc"


def test_history_to_ollama_pula_partes_vazias():
    contents = [types.Content(role="user", parts=[])]
    assert oc.history_to_ollama(contents) == []


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_generate_text_simples(app, monkeypatch):
    calls = []

    def _fake_post(url, json, timeout):
        calls.append((url, json))
        return _FakeResp(200, {"message": {"content": "resposta do modelo local"}})

    monkeypatch.setattr(oc.requests, "post", _fake_post)
    with app.app_context():
        app.config["OLLAMA_HOST"] = "http://127.0.0.1:11434"
        text = oc.generate_text("system", "prompt", model="qwen2.5:7b")
    assert text == "resposta do modelo local"
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1]["messages"][0] == {"role": "system", "content": "system"}


def test_generate_text_json_mode_seta_format(app, monkeypatch):
    seen = {}

    def _fake_post(url, json, timeout):
        seen.update(json)
        return _FakeResp(200, {"message": {"content": "{}"}})

    monkeypatch.setattr(oc.requests, "post", _fake_post)
    with app.app_context():
        oc.generate_text("system", "prompt", model="qwen2.5:7b", json_mode=True)
    assert seen["format"] == "json"


def test_post_chat_http_erro_vira_ollama_error(app, monkeypatch):
    def _fake_post(url, json, timeout):
        return _FakeResp(400, {"error": "qwen2.5:7b does not support tools"})

    monkeypatch.setattr(oc.requests, "post", _fake_post)
    with app.app_context():
        try:
            oc._post_chat({"model": "x", "messages": []})
            assert False, "deveria ter levantado OllamaError"
        except oc.OllamaError as exc:
            assert "does not support tools" in str(exc)


def _dispatch_stub(calls_log):
    def _dispatch(name, args, user_id):
        calls_log.append((name, args, user_id))
        return {"ok": True, "note_id": 1}
    return _dispatch


def test_run_agent_chama_tool_e_finaliza_com_texto(app, monkeypatch):
    """1ª resposta pede uma tool call; 2ª resposta só devolve texto — o loop
    despacha a tool, manda o resultado de volta e termina no texto final."""
    responses = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "criar_nota", "arguments": {"texto": "oi"}}}
        ]}},
        {"message": {"role": "assistant", "content": "Prontinho, anotei!"}},
    ]
    calls = []

    def _fake_post_chat(payload):
        calls.append(payload)
        return responses.pop(0)

    monkeypatch.setattr(oc, "_post_chat", _fake_post_chat)
    dispatch_calls = []

    with app.app_context():
        text, executed = oc.run_agent(
            user_id=1, api_key="", model="qwen2.5:7b", max_iters=5,
            system_instruction="sys", initial_contents=[],
            tool_declarations=_tool(),
            dispatch=_dispatch_stub(dispatch_calls),
        )

    assert text == "Prontinho, anotei!"
    assert executed is True
    assert dispatch_calls == [("criar_nota", {"texto": "oi"}, 1)]
    # 2 chamadas ao /api/chat: a que pediu a tool + a que devolveu o texto final
    assert len(calls) == 2
    # o resultado da tool foi anexado como mensagem role=tool antes da 2ª chamada
    tool_msgs = [m for m in calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert '"ok": true' in tool_msgs[0]["content"].lower() or "ok" in tool_msgs[0]["content"]


def test_run_agent_sem_tool_calls_retorna_texto_direto(app, monkeypatch):
    monkeypatch.setattr(oc, "_post_chat", lambda payload: {"message": {"content": "só texto, sem tools"}})
    with app.app_context():
        text, executed = oc.run_agent(
            user_id=1, api_key="", model="qwen2.5:7b", max_iters=5,
            system_instruction="sys", initial_contents=[],
            tool_declarations=_tool(),
            dispatch=_dispatch_stub([]),
        )
    assert text == "só texto, sem tools"
    assert executed is False


def test_run_agent_http_erro_vira_mensagem_amigavel_nao_excecao(app, monkeypatch):
    def _boom(payload):
        raise oc.OllamaError("qwen2.5:7b does not support tools")

    monkeypatch.setattr(oc, "_post_chat", _boom)
    with app.app_context():
        text, executed = oc.run_agent(
            user_id=1, api_key="", model="qwen2.5:7b", max_iters=5,
            system_instruction="sys", initial_contents=[],
            tool_declarations=_tool(),
            dispatch=_dispatch_stub([]),
        )
    assert "não consegui falar com o modelo local" in text
    assert executed is False


def test_run_agent_sem_modelo_configurado_nao_bate_na_rede(app, monkeypatch):
    def _should_not_be_called(payload):
        raise AssertionError("não deveria chamar a API sem modelo configurado")

    monkeypatch.setattr(oc, "_post_chat", _should_not_be_called)
    with app.app_context():
        text, executed = oc.run_agent(
            user_id=1, api_key="", model="", max_iters=5,
            tool_declarations=_tool(), dispatch=_dispatch_stub([]),
        )
    assert executed is False
    assert "helena models use" in text or "helena setup" in text
