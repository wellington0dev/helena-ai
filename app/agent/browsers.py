"""Detecção e abertura de navegador instalado — cross-platform.

Usado pela tool `abrir_navegador` (tarefa de desktop) e pela config "navegador
padrão" nas Configurações. Detecção é best-effort via binários conhecidos
(Linux/macOS) ou caminhos de instalação comuns (Windows) — sem depender de
registro do SO, que varia demais entre distros/versões.
"""
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

_SYSTEM = platform.system()

_UNIX_CANDIDATES = [
    ("firefox", "Firefox"),
    ("google-chrome-stable", "Google Chrome"),
    ("google-chrome", "Google Chrome"),
    ("chromium", "Chromium"),
    ("chromium-browser", "Chromium"),
    ("brave-browser", "Brave"),
    ("microsoft-edge-stable", "Microsoft Edge"),
    ("microsoft-edge", "Microsoft Edge"),
    ("opera", "Opera"),
    ("vivaldi-stable", "Vivaldi"),
    ("vivaldi", "Vivaldi"),
]

# (caminho relativo, rótulo, variável de ambiente que dá a raiz)
_WINDOWS_CANDIDATES = [
    (r"Google\Chrome\Application\chrome.exe", "Google Chrome", "ProgramFiles"),
    (r"Google\Chrome\Application\chrome.exe", "Google Chrome", "ProgramFiles(x86)"),
    (r"Mozilla Firefox\firefox.exe", "Firefox", "ProgramFiles"),
    (r"Microsoft\Edge\Application\msedge.exe", "Microsoft Edge", "ProgramFiles(x86)"),
    (r"Microsoft\Edge\Application\msedge.exe", "Microsoft Edge", "ProgramFiles"),
    (r"BraveSoftware\Brave-Browser\Application\brave.exe", "Brave", "LocalAppData"),
    (r"Opera\opera.exe", "Opera", "LocalAppData"),
    (r"Vivaldi\Application\vivaldi.exe", "Vivaldi", "LocalAppData"),
]


class BrowserError(Exception):
    pass


def detect_browsers() -> list[dict]:
    """Navegadores instalados encontrados: [{"id", "label", "path"}, ...]."""
    if _SYSTEM == "Windows":
        return _detect_windows()
    return _detect_unix()


def _detect_unix() -> list[dict]:
    found, seen = [], set()
    for bin_name, label in _UNIX_CANDIDATES:
        if label in seen:
            continue
        path = shutil.which(bin_name)
        if path:
            found.append({"id": bin_name, "label": label, "path": path})
            seen.add(label)
    return found


def _detect_windows() -> list[dict]:
    found, seen = [], set()
    for rel, label, env_var in _WINDOWS_CANDIDATES:
        if label in seen:
            continue
        root = os.environ.get(env_var)
        if not root:
            continue
        p = Path(root) / rel
        if p.exists():
            found.append({"id": p.stem, "label": label, "path": str(p)})
            seen.add(label)
    return found


def open_browser_for_user(user_id: int, url: str | None = None) -> str:
    """Abre o navegador PADRÃO do usuário (ou o 1º instalado); devolve o rótulo."""
    from app.extensions import db
    from app.models import User

    found = detect_browsers()
    if not found:
        raise BrowserError("nenhum navegador instalado foi encontrado neste computador")

    user = db.session.get(User, user_id)
    default_id = user.default_browser if user else None
    target = next((b for b in found if b["id"] == default_id), None) or found[0]

    cmd = [target["path"]]
    if url:
        cmd.append(url)
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        raise BrowserError(f"falha ao abrir {target['label']}: {exc}") from exc

    # dá um tempo pra janela mapear e traz pro workspace atual (crítico em
    # WMs com múltiplos workspaces — sem isso o print de conferência pode
    # mostrar outra janela em vez do navegador que acabou de abrir)
    from app.agent import desktop

    time.sleep(1.2)
    desktop.focus_app(target["id"])
    return target["label"]
