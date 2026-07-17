"""Ponto único de seleção de provider (app/agent/llm.py):
- desperta pro adapter certo dado LLM_PROVIDER;
- build_tool_declarations filtra generate_image/generate_audio fora do
  Gemini (o Ollama não faz nenhuma das duas — não pode nem tentar chamar)."""
from app.agent import llm, tools


def test_provider_default_e_gemini(app):
    with app.app_context():
        assert llm.provider() == "gemini"


def test_provider_le_do_config(app):
    app.config["LLM_PROVIDER"] = "ollama"
    with app.app_context():
        assert llm.provider() == "ollama"
    app.config["LLM_PROVIDER"] = "gemini"


def test_creds_gemini_usa_config_gemini(app):
    app.config["GEMINI_API_KEY"] = "chave-x"
    app.config["GEMINI_MODEL"] = "modelo-x"
    with app.app_context():
        assert llm.creds() == ("chave-x", "modelo-x")


def test_creds_ollama_ignora_gemini_key(app):
    app.config["LLM_PROVIDER"] = "ollama"
    app.config["OLLAMA_MODEL"] = "qwen2.5:7b"
    with app.app_context():
        assert llm.creds() == ("", "qwen2.5:7b")
    app.config["LLM_PROVIDER"] = "gemini"


def test_run_agent_despacha_pro_gemini_por_default(app, monkeypatch):
    calls = []
    monkeypatch.setattr(llm.gemini, "run_agent", lambda *a, **kw: calls.append(("gemini", a, kw)) or ("ok", True))
    monkeypatch.setattr(llm.ollama_client, "run_agent", lambda *a, **kw: calls.append(("ollama", a, kw)) or ("no", False))
    with app.app_context():
        text, executed = llm.run_agent(user_id=1, max_iters=5)
    assert calls[0][0] == "gemini"
    assert (text, executed) == ("ok", True)


def test_run_agent_despacha_pro_ollama_quando_configurado(app, monkeypatch):
    app.config["LLM_PROVIDER"] = "ollama"
    app.config["OLLAMA_MODEL"] = "qwen2.5:7b"
    calls = []
    monkeypatch.setattr(llm.gemini, "run_agent", lambda *a, **kw: calls.append(("gemini", a, kw)) or ("no", False))
    monkeypatch.setattr(llm.ollama_client, "run_agent", lambda *a, **kw: calls.append(("ollama", a, kw)) or ("ok", True))
    with app.app_context():
        text, executed = llm.run_agent(user_id=1, max_iters=5)
    app.config["LLM_PROVIDER"] = "gemini"
    assert calls[0][0] == "ollama"
    assert (text, executed) == ("ok", True)


def test_generate_text_despacha_pro_provider_certo(app, monkeypatch):
    app.config["LLM_PROVIDER"] = "ollama"
    app.config["OLLAMA_MODEL"] = "qwen2.5:7b"
    monkeypatch.setattr(llm.ollama_client, "generate_text", lambda *a, **kw: "resposta local")
    with app.app_context():
        assert llm.generate_text("sys", "prompt") == "resposta local"
    app.config["LLM_PROVIDER"] = "gemini"


def test_build_tool_declarations_gemini_inclui_geracao_de_midia(app, make_user):
    uid = make_user("u")
    with app.app_context():
        decls = tools.build_tool_declarations(uid, provider="gemini")
        names = {d.name for d in decls.function_declarations}
    assert "generate_image" in names
    assert "generate_audio" in names


def test_build_tool_declarations_ollama_exclui_geracao_de_midia(app, make_user):
    uid = make_user("u")
    with app.app_context():
        decls = tools.build_tool_declarations(uid, provider="ollama")
        names = {d.name for d in decls.function_declarations}
    assert "generate_image" not in names
    assert "generate_audio" not in names
    # o resto continua disponível — não é uma lista vazia por engano
    assert "create_note" in names
    assert "create_reminder" in names
