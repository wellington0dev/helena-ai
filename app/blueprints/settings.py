"""Configuração da Helena via web — equivalente do `helena setup`/`config`/
`provider`/`models`, pra quem prefere o navegador ao terminal.

Lê/escreve o MESMO `.env` que o `cli.py` usa, através de `env_file.py`
(módulo-raiz compartilhado — nunca reimplementar o parsing aqui). Mesma
lógica de Ollama do CLI, via `ollama_ctl.py`. Autenticação igual à do chat
(`@jwt_required()`, sem tier de permissão extra) — qualquer usuário logado
pode ver/mudar a configuração e reiniciar o servidor, mesmo nível de
confiança que o chat já tem hoje (shell/tela com aprovação).
"""
import os
import subprocess
import threading
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from flask_jwt_extended import jwt_required

import env_file
import local_models
import ollama_ctl

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")

# Chaves editáveis pela página — mesmo espírito do _KNOWN_ENV_KEYS do cli.py.
# NÃO inclui caminhos/banco (DATABASE_URL, HELENA_DATA_DIR, HELENA_MEDIA_DIR)
# nem JWT_SECRET_KEY: editar path/DB sem migrar os dados de verdade é um
# tiro no pé, e regenerar o JWT_SECRET_KEY deslogaria todo mundo — esses só
# aparecem como informativo (`info`), nunca editáveis por aqui.
EDITABLE_KEYS = [
    "LLM_PROVIDER", "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_IMAGE_MODEL",
    "GEMINI_TTS_MODEL", "GEMINI_TTS_VOICE", "OLLAMA_HOST", "OLLAMA_MODEL",
    "OLLAMA_MANAGED", "HELENA_PORT", "HELENA_HOST",
    "HELENA_DESKTOP_NOTIFICATIONS",
]
SECRET_KEYS = {"GEMINI_API_KEY"}
READONLY_INFO_KEYS = ["JWT_SECRET_KEY", "DATABASE_URL", "HELENA_DATA_DIR", "HELENA_MEDIA_DIR"]

# defaults espelhando CORE_FIELDS/ADVANCED_FIELDS/Config do cli.py/app/config.py
# — só pra mostrar um placeholder sensato quando a chave não está no .env
_DEFAULTS = {
    "LLM_PROVIDER": "gemini",
    "GEMINI_MODEL": "gemini-2.5-flash",
    "GEMINI_IMAGE_MODEL": "gemini-2.5-flash-image",
    "GEMINI_TTS_MODEL": "gemini-2.5-flash-preview-tts",
    "GEMINI_TTS_VOICE": "Kore",
    "OLLAMA_HOST": ollama_ctl.DEFAULT_HOST,
    "OLLAMA_MANAGED": "1",
    "HELENA_PORT": "5000",
    "HELENA_HOST": "0.0.0.0",
    "HELENA_DESKTOP_NOTIFICATIONS": "1",
}

# progresso de downloads em andamento (processo único — mesmo pressuposto
# documentado em app/extensions.py::write_lock)
_pull_status: dict[str, dict] = {}
_pull_lock = threading.Lock()


def _env_path() -> Path:
    return Path(current_app.config["ENV_FILE_PATH"])


@settings_bp.get("")
@jwt_required()
def get_settings():
    vals = env_file.read_env_values(_env_path())
    values = {}
    for key in EDITABLE_KEYS:
        raw = vals.get(key, "")
        if key in SECRET_KEYS:
            values[key] = env_file.mask_plain(raw)
        else:
            values[key] = raw or _DEFAULTS.get(key, "")
    info = {key: bool(vals.get(key)) for key in READONLY_INFO_KEYS}
    return jsonify(values=values, secrets=sorted(SECRET_KEYS), info=info), 200


@settings_bp.put("")
@jwt_required()
def update_settings():
    data = request.get_json(silent=True) or {}
    unknown = [k for k in data if k not in EDITABLE_KEYS]
    if unknown:
        return jsonify(error=f"chave(s) não editável(is): {', '.join(unknown)}"), 400
    updates = {}
    for key, value in data.items():
        value = "" if value is None else str(value).strip()
        if key in SECRET_KEYS and not value:
            continue  # vazio em campo secreto = "não mexer" (nunca apaga)
        updates[key] = value
    if updates:
        env_file.set_env_values(_env_path(), updates)
    return jsonify(ok=True, updated=sorted(updates.keys())), 200


@settings_bp.get("/ollama/models")
@jwt_required()
def ollama_models():
    hw = local_models.detect_hardware()
    installed = ollama_ctl.list_installed()
    active = env_file.read_env_values(_env_path()).get("OLLAMA_MODEL")
    catalog = [
        {
            "name": m["name"],
            "label": m["label"],
            "params_b": m["params_b"],
            "est_gb": m["est_gb"],
            "rating": local_models.rate_model(m, hw),
            "installed": m["name"] in installed,
            "active": m["name"] == active,
        }
        for m in local_models.CATALOG
    ]
    return jsonify(hardware=hw, catalog=catalog), 200


@settings_bp.post("/ollama/pull")
@jwt_required()
def ollama_pull():
    name = (request.get_json(silent=True) or {}).get("name", "").strip()
    if not name:
        return jsonify(error="name obrigatório"), 400
    with _pull_lock:
        if _pull_status.get(name, {}).get("status") == "downloading":
            return jsonify(ok=True, status="downloading"), 202
        _pull_status[name] = {"status": "downloading", "detail": ""}

    def _run() -> None:
        ok_ = ollama_ctl.pull(name, capture=True)
        with _pull_lock:
            _pull_status[name] = (
                {"status": "done", "detail": ""} if ok_
                else {"status": "error", "detail": "falha ao baixar — confira se o Ollama está instalado/rodando"}
            )

    threading.Thread(target=_run, daemon=True, name=f"ollama-pull-{name}").start()
    return jsonify(ok=True, status="downloading"), 202


@settings_bp.get("/ollama/pull/status")
@jwt_required()
def ollama_pull_status():
    name = request.args.get("name", "").strip()
    with _pull_lock:
        status = _pull_status.get(name, {"status": "unknown", "detail": ""})
    return jsonify(name=name, **status), 200


@settings_bp.post("/ollama/test")
@jwt_required()
def ollama_test():
    data = request.get_json(silent=True) or {}
    vals = env_file.read_env_values(_env_path())
    name = (data.get("name") or vals.get("OLLAMA_MODEL") or "").strip()
    if not name:
        return jsonify(ok=False, detail="nenhum modelo informado nem configurado"), 400
    host = vals.get("OLLAMA_HOST") or ollama_ctl.DEFAULT_HOST
    ollama_ctl.ensure_daemon(host)
    ok_, detail = ollama_ctl.smoke_test(host, name)
    return jsonify(ok=ok_, detail=detail), 200


@settings_bp.post("/restart")
@jwt_required()
def restart():
    """Reinicia o servidor sozinho, reusando 100% da lógica já testada do
    `cli.py` (detecta serviço systemd vs pidfile sozinho) — dispara o
    wrapper 'helena'/'helena.cmd' como um processo separado e devolve na
    hora; este processo Flask vai morrer em seguida (esperado).

    Limitação conhecida: só funciona de forma confiável quando a Helena está
    rodando via `helena start` (pidfile) ou como serviço instalado — em
    `helena test` (modo dev, sem pidfile) o `helena restart` não acha o
    processo antigo pra matar."""
    root = Path(current_app.root_path).parent
    wrapper = root / ("helena.cmd" if os.name == "nt" else "helena")
    if not wrapper.exists():
        return jsonify(error=f"wrapper não encontrado: {wrapper}"), 500
    kwargs: dict = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(root))
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen([str(wrapper), "restart"], **kwargs)
    except OSError as exc:
        return jsonify(error=str(exc)), 500
    return jsonify(ok=True), 202
