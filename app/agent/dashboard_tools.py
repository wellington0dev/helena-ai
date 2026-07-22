"""Tools `abrir_dashboard`/`fechar_dashboard`: a Helena abre/fecha a janela
nativa (Electron) do painel de desktop. Mesmo tier de mouse/teclado
(fullcontrol) — abrir uma janela na tela do usuário é uma ação de desktop,
mesmo espírito de `abrir_navegador` (`app/agent/desktop_task_tools.py`).

A "lógica" do painel inteira mora em `desktop-dashboard/main.js` (casca
Electron que só carrega `/dashboard`) — aqui só cuida de achar o binário,
subir o processo e derrubar depois. Estado de processo é module-level
(mesmo pressuposto de processo único já documentado em
`app/extensions.py::write_lock`)."""
import os
import subprocess
from pathlib import Path

from google.genai import types

from app.extensions import db
from app.models import User

ROOT = Path(__file__).resolve().parent.parent.parent
DASHBOARD_DIR = ROOT / "desktop-dashboard"

_proc: subprocess.Popen | None = None


ABRIR_DASHBOARD_DECL = types.FunctionDeclaration(
    name="abrir_dashboard",
    description=(
        "Abre a janela do PAINEL de desktop da Helena — um dashboard visual "
        "mostrando usuários ativos, jobs em segundo plano e recursos do "
        "sistema (CPU/RAM/disco/processos). Use quando o usuário pedir pra "
        "ver o painel/dashboard."
    ),
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)

FECHAR_DASHBOARD_DECL = types.FunctionDeclaration(
    name="fechar_dashboard",
    description="Fecha a janela do painel de desktop da Helena, se estiver aberta.",
    parameters=types.Schema(type=types.Type.OBJECT, properties={}),
)


def _deny(user_id: int) -> str | None:
    user = db.session.get(User, user_id)
    if user is None:
        return "usuário inválido"
    if not user.shell_full_control:
        return (
            "Abrir/fechar o painel exige CONTROLE ABSOLUTO (mesma régua de "
            "mouse/teclado). Explique ao usuário que ele precisa ativar esse "
            "nível (helena users fullcontrol) e não tente agir."
        )
    return None


def _electron_bin() -> Path | None:
    name = "electron.cmd" if os.name == "nt" else "electron"
    candidate = DASHBOARD_DIR / "node_modules" / ".bin" / name
    return candidate if candidate.exists() else None


def abrir_dashboard(user_id: int, args: dict) -> dict:
    global _proc
    err = _deny(user_id)
    if err:
        return {"ok": False, "error": err}

    if _proc is not None and _proc.poll() is None:
        return {"ok": True, "info": "o painel já está aberto"}

    electron = _electron_bin()
    if electron is None:
        return {
            "ok": False,
            "error": (
                "o painel ainda não foi instalado nesta máquina — rode "
                "'npm install' dentro de 'desktop-dashboard/' primeiro."
            ),
        }

    port = os.environ.get("HELENA_PORT", "5000")
    url = f"http://127.0.0.1:{port}/dashboard"
    try:
        _proc = subprocess.Popen(
            [str(electron), str(DASHBOARD_DIR), "--url", url],
            cwd=str(DASHBOARD_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=(os.name != "nt"),
        )
    except OSError as exc:
        return {"ok": False, "error": f"falha ao abrir o painel: {exc}"}

    from app import audit
    audit.record(user_id, "desktop", "abrir_dashboard")
    return {"ok": True, "info": "painel aberto"}


def fechar_dashboard(user_id: int, args: dict) -> dict:
    global _proc
    err = _deny(user_id)
    if err:
        return {"ok": False, "error": err}

    if _proc is None or _proc.poll() is not None:
        _proc = None
        return {"ok": True, "info": "o painel não estava aberto"}

    try:
        _proc.terminate()
        _proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _proc.kill()
    except OSError:
        pass
    _proc = None

    from app import audit
    audit.record(user_id, "desktop", "fechar_dashboard")
    return {"ok": True, "info": "painel fechado"}
