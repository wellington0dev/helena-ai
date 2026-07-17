"""Ponto único de seleção do provider de LLM (Gemini ou Ollama).

Todo call-site que roda o loop de tool-calling do agente ou pede um texto
solto pro "cérebro" da Helena passa por aqui — nunca importa `gemini`/
`ollama_client` diretamente pra isso. Exceção: geração de imagem/TTS
(`app/agent/generate.py`) e descrição de mídia enviada (`app/media/ingest.py`)
são capacidades Gemini-exclusivas por design (o Ollama não faz nenhuma das
duas) e continuam chamando o Gemini direto, independente do provider ativo.
"""
from flask import current_app

from app.agent import gemini, ollama_client


def provider() -> str:
    return current_app.config.get("LLM_PROVIDER", "gemini")


def creds() -> tuple[str, str]:
    """(api_key, model) do provider ATIVO — call-sites não precisam saber
    qual variável de ambiente checar."""
    cfg = current_app.config
    if provider() == "ollama":
        return "", cfg.get("OLLAMA_MODEL", "")
    return cfg["GEMINI_API_KEY"], cfg["GEMINI_MODEL"]


def run_agent(user_id: int, max_iters: int, **kwargs) -> tuple[str, bool]:
    api_key, model = creds()
    fn = ollama_client.run_agent if provider() == "ollama" else gemini.run_agent
    return fn(user_id, api_key, model, max_iters, **kwargs)


def generate_text(system_instruction: str, contents, *, json_mode: bool = False) -> str:
    api_key, model = creds()
    fn = ollama_client.generate_text if provider() == "ollama" else gemini.generate_text
    return fn(system_instruction, contents, api_key=api_key, model=model, json_mode=json_mode)
