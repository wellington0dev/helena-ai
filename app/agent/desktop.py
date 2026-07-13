"""Controle de desktop (tela/mouse/teclado), cross-platform.

Backends escolhidos em runtime:
- Windows / Linux-X11 / macOS: `pyautogui` (input) + `mss` (screenshot);
- Linux Wayland: `grim` (screenshot), `wtype` (teclado), `ydotool` (mouse) —
  ferramentas de sistema, pois o Wayland bloqueia injeção via bibliotecas Python.

Nada aqui levanta no import (as libs são importadas tarde) — só ao usar. Cada
função devolve erro claro se a capacidade não estiver disponível no ambiente.
"""
import os
import platform
import shutil
import subprocess

_SYSTEM = platform.system()  # Windows | Linux | Darwin


class DesktopError(Exception):
    pass


def is_wayland() -> bool:
    return (
        _SYSTEM == "Linux"
        and bool(os.environ.get("WAYLAND_DISPLAY"))
        and not os.environ.get("HELENA_FORCE_X11")
    )


def available() -> tuple[bool, str]:
    """Se o controle de desktop é possível aqui (e por quê não, se não)."""
    if _SYSTEM == "Linux" and not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return False, "sem sessão gráfica (o servidor precisa rodar no desktop logado, não headless/SSH)"
    if is_wayland() and not shutil.which("grim"):
        return False, "no Wayland é preciso instalar grim/wtype/ydotool (rode o install.sh)"
    return True, ""


def _run(cmd: list[str], timeout: int = 15, want_stdout: bool = False) -> bytes:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        raise DesktopError(f"'{cmd[0]}' não está instalado")
    except subprocess.TimeoutExpired:
        raise DesktopError(f"'{cmd[0]}' travou (timeout)")
    if r.returncode != 0:
        raise DesktopError(f"{cmd[0]} falhou: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout if want_stdout else b""


# --------------------------------------------------------------------------- #
# Screenshot
# --------------------------------------------------------------------------- #

def screenshot() -> tuple[bytes, int, int]:
    """Captura a tela → (png_bytes, largura, altura)."""
    png = _grim_png() if is_wayland() else _mss_png()
    w, h = _png_size(png)
    return png, w, h


def _grim_png() -> bytes:
    if not shutil.which("grim"):
        raise DesktopError("grim não instalado (necessário no Wayland)")
    return _run(["grim", "-"], want_stdout=True)


def _mss_png() -> bytes:
    import mss
    import mss.tools

    with mss.mss() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        img = sct.grab(mon)
        return mss.tools.to_png(img.rgb, img.size)


def _png_size(png: bytes) -> tuple[int, int]:
    # dimensões ficam no chunk IHDR (bytes 16..24), big-endian — sem depender de PIL
    if len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n":
        w = int.from_bytes(png[16:20], "big")
        h = int.from_bytes(png[20:24], "big")
        return w, h
    return 0, 0


# --------------------------------------------------------------------------- #
# Mouse
# --------------------------------------------------------------------------- #

_YDO_BTN = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}


def move_mouse(x: int, y: int) -> None:
    if is_wayland():
        _ydotool(["mousemove", "--absolute", "--", str(int(x)), str(int(y))])
    else:
        import pyautogui

        pyautogui.moveTo(int(x), int(y))


def click(button: str = "left", x: int | None = None, y: int | None = None, double: bool = False) -> None:
    if x is not None and y is not None:
        move_mouse(x, y)
    if is_wayland():
        code = _YDO_BTN.get(button, "0xC0")
        _ydotool(["click", code])
        if double:
            _ydotool(["click", code])
    else:
        import pyautogui

        pyautogui.click(button=button, clicks=2 if double else 1)


def scroll(amount: int) -> None:
    if is_wayland():
        _ydotool(["mousemove", "--wheel", "--", "0", str(int(amount))])
    else:
        import pyautogui

        pyautogui.scroll(int(amount))


def _ydotool(args: list[str]) -> None:
    if not shutil.which("ydotool"):
        raise DesktopError(
            "ydotool não está instalado/configurado (necessário p/ mouse no Wayland). "
            "Rode o install.sh e garanta que o daemon ydotoold está ativo."
        )
    env = dict(os.environ)
    # garante que o client fale com o socket do daemon (serviço de usuário)
    env.setdefault("YDOTOOL_SOCKET", f"/run/user/{os.getuid()}/.ydotool_socket")
    try:
        r = subprocess.run(["ydotool", *args], capture_output=True, timeout=15, env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise DesktopError(f"ydotool falhou: {exc}")
    if r.returncode != 0:
        raise DesktopError(
            "ydotool falhou (o daemon ydotoold está rodando? rode o install.sh): "
            + r.stderr.decode(errors="replace")[:160]
        )


# --------------------------------------------------------------------------- #
# Teclado
# --------------------------------------------------------------------------- #

def type_text(text: str) -> None:
    if not text:
        return
    if is_wayland():
        if shutil.which("wtype"):
            _run(["wtype", text])
        else:
            _ydotool(["type", text])
    else:
        import pyautogui

        pyautogui.typewrite(text, interval=0.01)


def press_key(combo: str) -> None:
    """Pressiona uma tecla ou atalho, ex.: 'enter', 'ctrl+c', 'alt+tab'."""
    keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
    if not keys:
        return
    if is_wayland():
        _wtype_combo(keys)
    else:
        import pyautogui

        pyautogui.hotkey(*keys)


# nomes comuns → nomes do wtype (xkb)
_WTYPE_KEYS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "tab": "Tab", "space": "space", "backspace": "BackSpace", "delete": "Delete",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next",
}
_WTYPE_MODS = {"ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
               "super": "logo", "win": "logo", "cmd": "logo"}


def _wtype_combo(keys: list[str]) -> None:
    if not shutil.which("wtype"):
        raise DesktopError("wtype não instalado (necessário p/ teclas no Wayland)")
    mods = [k for k in keys if k in _WTYPE_MODS]
    main = [k for k in keys if k not in _WTYPE_MODS]
    args = []
    for m in mods:
        args += ["-M", _WTYPE_MODS[m]]
    for k in main:
        args += ["-k", _WTYPE_KEYS.get(k, k)]
    for m in reversed(mods):
        args += ["-m", _WTYPE_MODS[m]]
    _run(["wtype", *args])
