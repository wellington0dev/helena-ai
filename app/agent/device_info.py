"""Contexto do dispositivo onde a Helena roda (SO, distro/versão, hardware).

Injetado no system_instruction para a Helena saber em que máquina está — útil
sobretudo para as tools de controle do computador (ela adapta comandos ao SO).
Calculado uma vez e cacheado; cada campo é defensivo para nunca derrubar o
contexto se algo não estiver disponível.
"""
import os
import platform

_cache: str | None = None


def _os_label() -> str:
    system = platform.system()  # Linux, Windows, Darwin
    if system == "Linux":
        try:
            rel = platform.freedesktop_os_release()  # Python 3.10+
            pretty = rel.get("PRETTY_NAME") or rel.get("NAME")
            if pretty:
                return f"Linux ({pretty}, kernel {platform.release()})"
        except (OSError, AttributeError):
            pass
        return f"Linux (kernel {platform.release()})"
    if system == "Windows":
        rel, ver, *_ = platform.win32_ver()
        return f"Windows {rel} (build {ver})".strip()
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}".strip()
    return system or "desconhecido"


def _ram_label() -> str | None:
    try:
        import psutil

        total = psutil.virtual_memory().total
        return f"{total / (1024 ** 3):.1f} GB"
    except Exception:  # noqa: BLE001
        return None


def _cpu_label() -> str:
    try:
        import psutil

        logical = psutil.cpu_count(logical=True)
        physical = psutil.cpu_count(logical=False)
        cores = f"{physical} núcleos/{logical} threads" if physical else f"{logical} threads"
    except Exception:  # noqa: BLE001
        cores = f"{os.cpu_count()} threads"
    name = platform.processor() or platform.machine()
    return f"{name} ({cores})".strip()


def _shell_label() -> str:
    if platform.system() == "Windows":
        return "cmd.exe / PowerShell"
    return os.environ.get("SHELL", "/bin/sh")


def _graphics_label() -> str | None:
    """Ambiente gráfico / gerenciador de janelas (só faz sentido no Linux)."""
    if platform.system() != "Linux":
        return None
    parts = []
    desktop = os.environ.get("XDG_CURRENT_DESKTOP") or os.environ.get("DESKTOP_SESSION")
    if desktop:
        parts.append(desktop)
    session = os.environ.get("XDG_SESSION_TYPE")  # wayland | x11
    if session:
        parts.append(session)
    # compositor/WM específico ajuda a Helena a escolher comandos (hyprctl, etc.)
    wm = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and "Hyprland"
    if not wm:
        for var, name in (("SWAYSOCK", "Sway"), ("I3SOCK", "i3")):
            if os.environ.get(var):
                wm = name
                break
    if wm and wm not in parts:
        parts.append(f"compositor {wm}")
    return ", ".join(parts) if parts else None


def device_context() -> str:
    """Bloco de texto com as infos do dispositivo (cacheado)."""
    global _cache
    if _cache is not None:
        return _cache

    lines = [f"- Sistema operacional: {_os_label()}"]
    lines.append(f"- Arquitetura: {platform.machine()}")
    graphics = _graphics_label()
    if graphics:
        lines.append(f"- Ambiente gráfico: {graphics}")
    lines.append(f"- Shell padrão: {_shell_label()}")
    lines.append(f"- CPU: {_cpu_label()}")
    ram = _ram_label()
    if ram:
        lines.append(f"- Memória RAM: {ram}")
    host = platform.node()
    if host:
        lines.append(f"- Nome da máquina: {host}")

    _cache = "\n".join(lines)
    return _cache
