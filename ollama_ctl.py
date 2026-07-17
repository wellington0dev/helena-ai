#!/usr/bin/env python3
"""Controle do Ollama (instalar/subir daemon/baixar-remover modelo/testar) —
stdlib-only, compartilhado entre `cli.py` e o blueprint web
`app/blueprints/settings.py` (fonte única de verdade).

Sem print()/cor aqui de propósito: quem chama decide como apresentar
(cli.py imprime no terminal formatado, o blueprint devolve JSON)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

DEFAULT_HOST = "http://127.0.0.1:11434"
IS_WIN = os.name == "nt"


def reachable(host: str = DEFAULT_HOST, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{host}/api/version", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001 — qualquer falha de rede é "não alcançável"
        return False


def list_installed() -> set[str]:
    if not shutil.which("ollama"):
        return set()
    try:
        r = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if r.returncode != 0:
        return set()
    names = set()
    for line in r.stdout.splitlines()[1:]:  # pula o cabeçalho "NAME ..."
        parts = line.split()
        if parts:
            names.add(parts[0])
    return names


def pull(name: str, *, capture: bool = False) -> bool:
    """Baixa um modelo. `capture=False` (default, uso no terminal) deixa a
    barra de progresso nativa do Ollama aparecer no stdout herdado;
    `capture=True` (uso do blueprint web, saída não tem pra onde ir) engole."""
    try:
        r = subprocess.run(["ollama", "pull", name], capture_output=capture)
    except OSError:
        return False
    return r.returncode == 0


def rm(name: str) -> tuple[bool, str]:
    r = subprocess.run(["ollama", "rm", name], capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr.strip() or "falha ao remover")
    return True, ""


def smoke_test(host: str, model: str, timeout: float = 90.0) -> tuple[bool, str]:
    """Testa de verdade se o modelo RODA, não só se foi baixado — pede uma
    geração mínima. 'ollama pull' com sucesso NÃO garante que o runtime
    funciona: instalações incompletas do Ollama (ex.: binário llama-server
    ausente ou sem permissão de leitura) só quebram nessa hora, em silêncio,
    deixando a configuração apontando pra algo que não funciona."""
    body = json.dumps({"model": model, "prompt": "oi", "stream": False}).encode()
    req = urllib.request.Request(
        f"{host}/api/generate", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        try:
            detail = json.loads(detail).get("error", detail)
        except ValueError:
            pass
        return False, detail or f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 — qualquer falha de rede/parse é "não funcionou"
        return False, str(exc)
    if not (data.get("response") or "").strip():
        return False, "resposta vazia do modelo"
    return True, ""


def ensure_daemon(host: str = DEFAULT_HOST, *, wait_seconds: float = 10.0) -> bool:
    """Sobe 'ollama serve' se ainda não estiver respondendo. True se (já
    estava ou ficou) alcançável dentro do prazo."""
    if reachable(host):
        return True
    try:
        kwargs: dict = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(["ollama", "serve"], **kwargs)
    except OSError:
        return False
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if reachable(host):
            return True
        time.sleep(0.5)
    return False


def install() -> tuple[bool, str]:
    """Instala o Ollama via instalador oficial (Linux/macOS — Windows não
    tem instalador silencioso; quem chama trata esse caso à parte checando
    IS_WIN antes de chamar). Devolve (sucesso, mensagem de erro se falhou)."""
    if shutil.which("ollama"):
        return True, ""
    try:
        r = subprocess.run(["sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"])
    except OSError as exc:
        return False, str(exc)
    if r.returncode != 0 or not shutil.which("ollama"):
        return False, "instalação falhou — instale manualmente: https://ollama.com/download"
    return True, ""
