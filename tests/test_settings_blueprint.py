"""Blueprint /settings: equivalente web do cli.py (setup/config/provider/models).
Sempre contra o .env de MENTIRA que o conftest.py aponta via HELENA_ENV_FILE —
nunca o .env de verdade do repositório."""
import time

import env_file
import ollama_ctl
from app.config import Config


def _env_path():
    return Config.ENV_FILE_PATH


def test_get_settings_exige_auth(client):
    r = client.get("/settings")
    assert r.status_code == 401


def test_get_settings_devolve_defaults_quando_env_vazio(client, make_user, auth):
    uid = make_user("u")
    r = client.get("/settings", headers=auth(uid))
    assert r.status_code == 200
    data = r.get_json()
    assert data["values"]["LLM_PROVIDER"] == "gemini"
    assert data["values"]["HELENA_PORT"] == "5000"
    assert "GEMINI_API_KEY" in data["secrets"]


def test_put_settings_rejeita_chave_desconhecida(client, make_user, auth):
    uid = make_user("u")
    r = client.put("/settings", json={"JWT_SECRET_KEY": "hax"}, headers=auth(uid))
    assert r.status_code == 400
    assert "JWT_SECRET_KEY" in r.get_json()["error"]


def test_put_settings_grava_e_persiste(client, make_user, auth):
    uid = make_user("u")
    r = client.put("/settings", json={"GEMINI_TTS_VOICE": "Puck"}, headers=auth(uid))
    assert r.status_code == 200
    assert env_file.read_env_values(_env_path())["GEMINI_TTS_VOICE"] == "Puck"

    r2 = client.get("/settings", headers=auth(uid))
    assert r2.get_json()["values"]["GEMINI_TTS_VOICE"] == "Puck"


def test_get_settings_mascara_segredo(client, make_user, auth):
    uid = make_user("u")
    env_file.set_env_values(_env_path(), {"GEMINI_API_KEY": "AIzaSyABCDEFGHIJKL"})
    r = client.get("/settings", headers=auth(uid))
    shown = r.get_json()["values"]["GEMINI_API_KEY"]
    assert shown != "AIzaSyABCDEFGHIJKL"
    assert shown.startswith("AIza")
    assert "•" in shown


def test_put_settings_vazio_em_segredo_nao_apaga(client, make_user, auth):
    uid = make_user("u")
    env_file.set_env_values(_env_path(), {"GEMINI_API_KEY": "chave-existente"})
    r = client.put("/settings", json={"GEMINI_API_KEY": "", "GEMINI_MODEL": "gemini-3"}, headers=auth(uid))
    assert r.status_code == 200
    vals = env_file.read_env_values(_env_path())
    assert vals["GEMINI_API_KEY"] == "chave-existente"
    assert vals["GEMINI_MODEL"] == "gemini-3"


def test_ollama_models_lista_catalogo(client, make_user, auth, monkeypatch):
    uid = make_user("u")
    monkeypatch.setattr(ollama_ctl, "list_installed", lambda: {"qwen2.5:1.5b"})
    r = client.get("/settings/ollama/models", headers=auth(uid))
    assert r.status_code == 200
    data = r.get_json()
    assert "hardware" in data
    names = [m["name"] for m in data["catalog"]]
    assert "qwen2.5:1.5b" in names
    row = next(m for m in data["catalog"] if m["name"] == "qwen2.5:1.5b")
    assert row["installed"] is True
    assert row["rating"] in ("green", "yellow", "red")


def test_ollama_pull_e_status(client, make_user, auth, monkeypatch):
    uid = make_user("u")
    monkeypatch.setattr(ollama_ctl, "pull", lambda name, capture=False: True)
    r = client.post("/settings/ollama/pull", json={"name": "qwen2.5:1.5b"}, headers=auth(uid))
    assert r.status_code == 202

    for _ in range(50):
        status = client.get(
            "/settings/ollama/pull/status", query_string={"name": "qwen2.5:1.5b"}, headers=auth(uid)
        ).get_json()
        if status["status"] == "done":
            break
        time.sleep(0.02)
    assert status["status"] == "done"


def test_ollama_pull_status_desconhecido(client, make_user, auth):
    uid = make_user("u")
    r = client.get("/settings/ollama/pull/status", query_string={"name": "nunca-pedido"}, headers=auth(uid))
    assert r.get_json()["status"] == "unknown"


def test_ollama_test_usa_modelo_configurado(client, make_user, auth, monkeypatch):
    uid = make_user("u")
    env_file.set_env_values(_env_path(), {"OLLAMA_MODEL": "qwen2.5:1.5b"})
    monkeypatch.setattr(ollama_ctl, "ensure_daemon", lambda host, **k: True)
    monkeypatch.setattr(ollama_ctl, "smoke_test", lambda host, model, **k: (True, ""))
    r = client.post("/settings/ollama/test", json={}, headers=auth(uid))
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_ollama_test_sem_modelo_devolve_400(client, make_user, auth):
    uid = make_user("u")
    env_file.set_env_values(_env_path(), {"OLLAMA_MODEL": ""})
    r = client.post("/settings/ollama/test", json={}, headers=auth(uid))
    assert r.status_code == 400


def test_restart_dispara_wrapper_sem_bloquear(client, make_user, auth, monkeypatch):
    uid = make_user("u")
    calls = []
    monkeypatch.setattr(
        "app.blueprints.settings.subprocess.Popen",
        lambda args, **kwargs: calls.append(args) or object(),
    )
    r = client.post("/settings/restart", headers=auth(uid))
    assert r.status_code == 202
    assert len(calls) == 1
    assert calls[0][1] == "restart"
