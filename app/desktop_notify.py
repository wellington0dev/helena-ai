"""Notificações nativas do desktop (toast/balão do SO), cross-platform.

Best-effort: nunca deve derrubar quem chama. Igual em espírito ao
app/agent/desktop.py — backend escolhido em runtime, nada de novo pip:
- Linux: `notify-send` (libnotify — funciona em X11 e Wayland via D-Bus);
- macOS: `osascript` (sempre presente, sem instalar nada);
- Windows: PowerShell + balão do NotifyIcon (WinForms, sem módulo extra).

Se o servidor roda headless (VPS/SSH, sem sessão gráfica), as chamadas aqui
simplesmente falham silenciosamente — mesmo espírito do desktop.available().
"""
import os
import platform
import shutil
import subprocess
import tempfile

_SYSTEM = platform.system()  # Windows | Linux | Darwin
_TIMEOUT = 5


class DesktopNotifyError(Exception):
    pass


def available() -> bool:
    if _SYSTEM == "Linux":
        return bool(shutil.which("notify-send"))
    if _SYSTEM == "Darwin":
        return bool(shutil.which("osascript"))
    if _SYSTEM == "Windows":
        return bool(shutil.which("powershell") or shutil.which("powershell.exe"))
    return False


def notify(title: str, body: str) -> None:
    """Dispara uma notificação nativa. Levanta DesktopNotifyError se não deu —
    quem chama decide se loga/ignora (ver notifications_dispatcher.py)."""
    try:
        if _SYSTEM == "Linux":
            _notify_linux(title, body)
        elif _SYSTEM == "Darwin":
            _notify_macos(title, body)
        elif _SYSTEM == "Windows":
            _notify_windows(title, body)
        else:
            raise DesktopNotifyError(f"SO não suportado: {_SYSTEM}")
    except DesktopNotifyError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DesktopNotifyError(str(exc))


def _notify_linux(title: str, body: str) -> None:
    if not shutil.which("notify-send"):
        raise DesktopNotifyError(
            "notify-send não instalado (pacote libnotify-bin/libnotify)"
        )
    r = subprocess.run(
        ["notify-send", "--app-name=Helena", "--icon=dialog-information", title, body],
        capture_output=True, timeout=_TIMEOUT,
    )
    if r.returncode != 0:
        raise DesktopNotifyError(r.stderr.decode(errors="replace")[:200] or "notify-send falhou")


def _notify_macos(title: str, body: str) -> None:
    if not shutil.which("osascript"):
        raise DesktopNotifyError("osascript indisponível")
    script = (
        f'display notification {_applescript_str(body)} '
        f'with title {_applescript_str(title)}'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, timeout=_TIMEOUT)
    if r.returncode != 0:
        raise DesktopNotifyError(r.stderr.decode(errors="replace")[:200] or "osascript falhou")


def _applescript_str(s: str) -> str:
    # escapa aspas/backslash — os textos vêm da IA, nunca confiar sem escapar
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# script estático (sem interpolação) — título/corpo chegam via -Title/-Body,
# como argumentos de processo separados, nunca concatenados no texto do script
# (evita injeção de PowerShell com texto vindo da IA).
_PS_SCRIPT = """
param([string]$Title, [string]$Body)
Add-Type -AssemblyName System.Windows.Forms
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.Visible = $true
$notify.BalloonTipTitle = $Title
$notify.BalloonTipText = $Body
$notify.ShowBalloonTip(8000)
Start-Sleep -Seconds 1
$notify.Dispose()
"""


def _notify_windows(title: str, body: str) -> None:
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        raise DesktopNotifyError("powershell não encontrado")
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as f:
        f.write(_PS_SCRIPT)
        script_path = f.name
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-Title", title, "-Body", body],
            capture_output=True, timeout=_TIMEOUT + 2,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    if r.returncode != 0:
        raise DesktopNotifyError(r.stderr.decode(errors="replace")[:200] or "powershell falhou")
