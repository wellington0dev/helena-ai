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
import time

_SYSTEM = platform.system()  # Windows | Linux | Darwin


class DesktopError(Exception):
    pass


def focus_app(app_id: str) -> None:
    """Best-effort: traz a janela do app pra frente/workspace atual. CRÍTICO em
    WMs com múltiplos workspaces (Hyprland/Sway) — sem isso, abrir um app pode
    deixá-lo noutro workspace enquanto capturar_tela continua mostrando o que
    já estava em foco, e a IA "vê" e mira na janela errada. Não levanta erro se
    o WM não suportar — é um empurrão, não uma garantia."""
    if not is_wayland():
        return
    try:
        if shutil.which("hyprctl"):
            for _ in range(3):
                r = subprocess.run(
                    ["hyprctl", "dispatch", "focuswindow", f"class:{app_id}"],
                    capture_output=True, timeout=5,
                )
                if r.returncode == 0 and b"no such window" not in r.stdout.lower():
                    break
                time.sleep(0.4)
        elif shutil.which("swaymsg"):
            subprocess.run(
                ["swaymsg", f'[app_id="{app_id}"] focus'],
                capture_output=True, timeout=5,
            )
    except (OSError, subprocess.TimeoutExpired):
        pass  # best-effort — não trava a ação por causa disso


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

_screen_wh: tuple[int, int] | None = None  # resolução REAL da tela (p/ mapear cliques)
_last_shot_wh: tuple[int, int] | None = None  # dimensões do print ENVIADO ao modelo

# largura máxima do print enviado ao modelo — reduz tokens de imagem (Gemini
# cobra por "tile"; acima disso o ganho de nitidez não compensa o custo). Abaixo
# disso mantém o PNG original (não faz upscale).
_MAX_SHOT_WIDTH = 1280


def screenshot() -> tuple[bytes, int, int]:
    """Captura a tela → (png_bytes, largura, altura) do print DEVOLVIDO (pode ser
    menor que a tela real, para economizar tokens). Use `report_to_real` para
    converter coordenadas miradas nesse print de volta para pixels reais."""
    global _screen_wh, _last_shot_wh
    png = _grim_png() if is_wayland() else _mss_png()
    rw, rh = _png_size(png)
    if rw and rh:
        _screen_wh = (rw, rh)
    png, ow, oh = _downscale_png(png, rw, rh)
    _last_shot_wh = (ow, oh) if ow and oh else (rw, rh)
    return png, ow, oh


def _downscale_png(png: bytes, w: int, h: int) -> tuple[bytes, int, int]:
    if not w or w <= _MAX_SHOT_WIDTH:
        return png, w, h
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(png))
    scale = _MAX_SHOT_WIDTH / w
    new_wh = (_MAX_SHOT_WIDTH, max(1, round(h * scale)))
    im = im.resize(new_wh, Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), new_wh[0], new_wh[1]


def screen_size() -> tuple[int, int]:
    """Resolução REAL da tela (não a do print reduzido) — usada internamente
    para mapear cliques em pixels absolutos."""
    if _screen_wh is None:
        screenshot()  # popula o cache
    return _screen_wh or (1920, 1080)


def report_to_real(x: int, y: int) -> tuple[int, int]:
    """Converte uma coordenada mirada no ÚLTIMO PRINT (o que o modelo viu) para
    pixels REAIS da tela. Necessário quando o print enviado é menor que a tela
    (economia de tokens) — sem isso o clique cai no lugar errado."""
    rw, rh = screen_size()
    ow, oh = _last_shot_wh or (rw, rh)
    if not ow or not oh:
        return int(x), int(y)
    return round(x / ow * rw), round(y / oh * rh)


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


def _abs_coords(x: int, y: int) -> tuple[int, int]:
    """Pixel da tela → range absoluto do uinput do ydotool (0..ABS_MAX).
    O ydotool `mousemove --absolute` NÃO usa pixels; o compositor mapeia o range
    para a tela toda. Sem essa conversão, o clique cai no lugar errado.
    (Se seu setup mapear pixels direto, use HELENA_YDOTOOL_ABS_MAX=0.)"""
    abs_max = int(os.environ.get("HELENA_YDOTOOL_ABS_MAX", "65535"))
    if abs_max <= 0:
        return int(x), int(y)
    w, h = screen_size()
    ax = max(0, min(abs_max, round(int(x) / max(w, 1) * abs_max)))
    ay = max(0, min(abs_max, round(int(y) / max(h, 1) * abs_max)))
    return ax, ay


def _hyprctl_cursorpos() -> tuple[int, int] | None:
    """Posição REAL do cursor via Hyprland — usada como feedback de precisão.
    None se não for Hyprland ou o comando falhar (cai pro modo --absolute cego)."""
    if not shutil.which("hyprctl"):
        return None
    try:
        r = subprocess.run(["hyprctl", "cursorpos"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return None
        xs, ys = r.stdout.strip().split(",")
        return int(xs.strip()), int(ys.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _ydotool_move_relative(dx: float, dy: float) -> None:
    _ydotool(["mousemove", "--", str(round(dx)), str(round(dy))])


def _move_mouse_wayland(x: int, y: int) -> None:
    """Move o cursor pra (x,y) via ydotool.

    Em alguns setups o device virtual do ydotoold só tem capacidade RELATIVA
    (sem ABS_X/ABS_Y) — `mousemove --absolute` aí não funciona: o valor vira um
    delta relativo enorme e o cursor sempre bate na borda da tela, não importa
    o alvo (bug real observado: qualquer coordenada != perto de 0 saturava no
    canto). Sem uma forma confiável de detectar a capacidade do device de fora,
    a estratégia é: 1) "home" pra um canto conhecido (delta relativo gigante,
    sempre clampa); 2) se `hyprctl` estiver disponível (Hyprland), corrige por
    FEEDBACK — mede a posição real e reenvia a diferença amortecida até
    convergir; isso funciona mesmo com aceleração de ponteiro não-linear no
    delta relativo (observado ~2x, não-constante), sem precisar calibrar esse
    fator. Sem hyprctl (outro compositor), cai pro --absolute direto.
    """
    if _hyprctl_cursorpos() is None:
        ax, ay = _abs_coords(x, y)
        _ydotool(["mousemove", "--absolute", "--", str(ax), str(ay)])
        return

    _ydotool_move_relative(-99999, -99999)  # home: clampa num canto conhecido
    time.sleep(0.05)
    pos = _hyprctl_cursorpos() or (0, 0)

    for _ in range(6):
        err_x, err_y = x - pos[0], y - pos[1]
        if abs(err_x) <= 2 and abs(err_y) <= 2:
            break
        # amortece (só metade do erro) — converge mesmo sem saber o ganho exato
        _ydotool_move_relative(err_x * 0.5, err_y * 0.5)
        time.sleep(0.05)
        pos = _hyprctl_cursorpos() or pos


def move_mouse(x: int, y: int) -> None:
    if is_wayland():
        _move_mouse_wayland(int(x), int(y))
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
