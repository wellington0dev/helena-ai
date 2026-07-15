"""Detecção/abertura de navegador — sem navegador nenhum instalado deve falhar
com erro claro, não estourar; o navegador padrão do usuário é respeitado."""
import pytest

from app.agent import browsers, desktop


def _patch_popen(monkeypatch, opened: dict | None = None):
    """Evita processo real + o sleep/focus_app (que dispara hyprctl de verdade
    nesta máquina) — subprocess é o MESMO módulo compartilhado por desktop.py,
    então mockar Popen globalmente quebraria subprocess.run() usado lá dentro."""
    monkeypatch.setattr(
        browsers.subprocess, "Popen",
        lambda cmd, **kw: (opened.setdefault("cmd", cmd) if opened is not None else None),
    )
    monkeypatch.setattr(desktop, "focus_app", lambda app_id: None)
    monkeypatch.setattr(browsers.time, "sleep", lambda s: None)


def test_open_sem_navegador_instalado_falha_com_erro_claro(monkeypatch):
    monkeypatch.setattr(browsers, "detect_browsers", lambda: [])
    with pytest.raises(browsers.BrowserError):
        browsers.open_browser_for_user(1)


def test_open_usa_padrao_do_usuario(app, make_user, monkeypatch):
    uid = make_user("u")
    with app.app_context():
        from app.extensions import db, write_lock
        from app.models import User

        with write_lock:
            db.session.get(User, uid).default_browser = "firefox"
            db.session.commit()

        monkeypatch.setattr(
            browsers, "detect_browsers",
            lambda: [
                {"id": "chromium", "label": "Chromium", "path": "/bin/chromium"},
                {"id": "firefox", "label": "Firefox", "path": "/bin/firefox"},
            ],
        )
        opened = {}
        _patch_popen(monkeypatch, opened)
        label = browsers.open_browser_for_user(uid, url="https://x.com")
        assert label == "Firefox"
        assert opened["cmd"] == ["/bin/firefox", "https://x.com"]


def test_open_sem_padrao_usa_primeiro_instalado(app, make_user, monkeypatch):
    uid = make_user("u2")
    with app.app_context():
        monkeypatch.setattr(
            browsers, "detect_browsers",
            lambda: [{"id": "chromium", "label": "Chromium", "path": "/bin/chromium"}],
        )
        _patch_popen(monkeypatch)
        label = browsers.open_browser_for_user(uid)
        assert label == "Chromium"
