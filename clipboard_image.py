#!/usr/bin/env python3
"""Lê uma imagem da área de transferência do SO (ex.: screenshot copiado),
pro comando `/imagem` do chat CLI anexar sem precisar salvar arquivo antes.

Roda no lado do CLIENTE (onde `helena chat` é chamado — pode ser uma máquina
diferente da que roda o servidor), por isso fica na raiz junto dos outros
módulos de CLI, não dentro de app/. Mesmo espírito do app/desktop_notify.py:
best-effort, backend escolhido em runtime por SO, nada de novo pip — só
ferramentas de sistema via subprocess."""
import os
import platform
import shutil
import subprocess
import tempfile

_SYSTEM = platform.system()  # Windows | Linux | Darwin
_TIMEOUT = 5


class ClipboardImageError(Exception):
    pass


def read_clipboard_image() -> bytes | None:
    """PNG bytes se houver imagem na área de transferência, None se o
    clipboard só tem texto (ou está vazio) — isso NÃO é erro. Levanta
    ClipboardImageError só se a ferramenta de SO necessária estiver
    ausente ou falhar de verdade."""
    try:
        if _SYSTEM == "Linux":
            return _read_linux()
        if _SYSTEM == "Darwin":
            return _read_macos()
        if _SYSTEM == "Windows":
            return _read_windows()
        raise ClipboardImageError(f"SO não suportado: {_SYSTEM}")
    except ClipboardImageError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ClipboardImageError(str(exc))


def _read_linux() -> bytes | None:
    if os.environ.get("WAYLAND_DISPLAY"):
        if not shutil.which("wl-paste"):
            raise ClipboardImageError(
                "wl-paste não instalado (pacote wl-clipboard) — necessário pra "
                "colar imagem no Wayland."
            )
        r = subprocess.run(
            ["wl-paste", "--type", "image/png"], capture_output=True, timeout=_TIMEOUT,
        )
        if r.returncode != 0:
            return None  # clipboard sem imagem (wl-paste devolve erro nesse caso)
        return r.stdout or None

    if not shutil.which("xclip"):
        raise ClipboardImageError(
            "xclip não instalado (pacote xclip) — necessário pra colar imagem no X11."
        )
    check = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
        capture_output=True, timeout=_TIMEOUT,
    )
    targets = check.stdout.decode(errors="replace")
    if "image/png" not in targets:
        return None  # clipboard sem imagem
    r = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        capture_output=True, timeout=_TIMEOUT,
    )
    if r.returncode != 0:
        return None
    return r.stdout or None


_APPLESCRIPT = """
try
    set pngData to the clipboard as «class PNGf»
on error
    return "NOIMAGE"
end try
set theFile to open for access POSIX file "%s" with write permission
set eof theFile to 0
write pngData to theFile
close access theFile
return "OK"
"""


def _read_macos() -> bytes | None:
    if not shutil.which("osascript"):
        raise ClipboardImageError("osascript indisponível")
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp_path = f.name
    try:
        script = _APPLESCRIPT % tmp_path
        r = subprocess.run(
            ["osascript", "-e", script], capture_output=True, timeout=_TIMEOUT, text=True,
        )
        if r.returncode != 0:
            raise ClipboardImageError(r.stderr.strip()[:200] or "osascript falhou")
        if "NOIMAGE" in r.stdout:
            return None
        with open(tmp_path, "rb") as f:
            data = f.read()
        return data or None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# script estático (sem interpolação) — o caminho do arquivo chega via
# -Path, como argumento de processo separado (evita injeção de PowerShell).
_PS_SCRIPT = """
param([string]$Path)
Add-Type -AssemblyName System.Windows.Forms
$img = [System.Windows.Forms.Clipboard]::GetImage()
if ($null -eq $img) {
    Write-Output "NOIMAGE"
} else {
    $img.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output "OK"
}
"""


def _read_windows() -> bytes | None:
    ps = shutil.which("powershell") or shutil.which("powershell.exe")
    if not ps:
        raise ClipboardImageError("powershell não encontrado")
    with tempfile.NamedTemporaryFile(suffix=".ps1", delete=False, mode="w", encoding="utf-8") as f:
        f.write(_PS_SCRIPT)
        script_path = f.name
    tmp_path = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script_path, "-Path", tmp_path],
            capture_output=True, timeout=_TIMEOUT + 2, text=True,
        )
        if r.returncode != 0:
            raise ClipboardImageError(r.stderr.strip()[:200] or "powershell falhou")
        if "NOIMAGE" in r.stdout:
            return None
        with open(tmp_path, "rb") as f:
            data = f.read()
        return data or None
    finally:
        for p in (script_path, tmp_path):
            try:
                os.unlink(p)
            except OSError:
                pass
