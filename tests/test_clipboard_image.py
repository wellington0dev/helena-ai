"""clipboard_image.read_clipboard_image: dispatch por SO, sempre com
subprocess/shutil.which monkeypatchados — nunca lê a área de transferência
de verdade em teste (mesmo padrão de tests/test_desktop_notify.py)."""
import subprocess

import clipboard_image
from clipboard_image import ClipboardImageError


def _result(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_linux_wayland_sem_wl_paste_da_erro_claro(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: None)
    try:
        clipboard_image.read_clipboard_image()
        assert False, "deveria ter levantado ClipboardImageError"
    except ClipboardImageError as e:
        assert "wl-clipboard" in str(e)


def test_linux_wayland_le_imagem(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: "/usr/bin/" + name)
    png_bytes = b"\x89PNG-fake-bytes"
    monkeypatch.setattr(
        clipboard_image.subprocess, "run", lambda *a, **k: _result(0, png_bytes)
    )
    assert clipboard_image.read_clipboard_image() == png_bytes


def test_linux_wayland_sem_imagem_no_clipboard(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        clipboard_image.subprocess, "run", lambda *a, **k: _result(1, b"", b"no selection")
    )
    assert clipboard_image.read_clipboard_image() is None


def test_linux_x11_sem_xclip_da_erro_claro(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: None)
    try:
        clipboard_image.read_clipboard_image()
        assert False, "deveria ter levantado ClipboardImageError"
    except ClipboardImageError as e:
        assert "xclip" in str(e)


def test_linux_x11_le_imagem_quando_targets_tem_png(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: "/usr/bin/" + name)
    png_bytes = b"\x89PNG-fake-bytes"
    calls = []

    def _fake_run(args, **kwargs):
        calls.append(args)
        if "TARGETS" in args:
            return _result(0, b"image/png\ntext/plain\n")
        return _result(0, png_bytes)

    monkeypatch.setattr(clipboard_image.subprocess, "run", _fake_run)
    assert clipboard_image.read_clipboard_image() == png_bytes
    assert len(calls) == 2


def test_linux_x11_sem_imagem_no_clipboard(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "Linux")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(clipboard_image.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(
        clipboard_image.subprocess, "run", lambda *a, **k: _result(0, b"text/plain\n")
    )
    assert clipboard_image.read_clipboard_image() is None


def test_sistema_nao_suportado(monkeypatch):
    monkeypatch.setattr(clipboard_image, "_SYSTEM", "PlanNine")
    try:
        clipboard_image.read_clipboard_image()
        assert False, "deveria ter levantado ClipboardImageError"
    except ClipboardImageError:
        pass
