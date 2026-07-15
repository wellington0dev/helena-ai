"""Execução de código em SANDBOX (bubblewrap) — para código escrito pela IA.

Isolamento (verificado por testes negativos): SEM rede (`--unshare-all` inclui a
rede), SEM acesso ao FS do host (só /usr read-only + /tmp em tmpfs; nada de /home,
.env, .ssh), namespaces próprios (pid/ipc/uts), e timeout que mata a árvore
(`--die-with-parent` + pid namespace). É isto que torna seguro rodar código
autônomo no loop de pesquisa — NÃO confundir com o `executar_shell`, que roda
sem sandbox no host (por isso ele exige permissão/aprovação).
"""
import shutil
import subprocess

from flask import current_app
from google.genai import types

_BWRAP_BASE = [
    "bwrap",
    "--ro-bind", "/usr", "/usr",
    "--symlink", "usr/lib", "/lib",
    "--symlink", "usr/lib64", "/lib64",
    "--symlink", "usr/bin", "/bin",
    "--symlink", "usr/bin", "/sbin",
    "--proc", "/proc",
    "--dev", "/dev",
    "--tmpfs", "/tmp",
    "--chdir", "/tmp",
    "--unshare-all",       # sem rede/pid/ipc/uts do host
    "--die-with-parent",   # morre junto (mata fork-bomb no timeout)
]

_INTERP = {
    "python": ["/usr/bin/python3", "-c"],
    "python3": ["/usr/bin/python3", "-c"],
    "bash": ["/bin/bash", "-c"],
    "sh": ["/bin/sh", "-c"],
    "node": ["/usr/bin/node", "-e"],
    "javascript": ["/usr/bin/node", "-e"],
}


def run_sandboxed(language: str, code: str) -> dict:
    """Roda `code` isolado. Devolve {ok, exit_code, stdout, stderr, timeout}."""
    if not shutil.which("bwrap"):
        return {"ok": False, "error": "bubblewrap (bwrap) não instalado — sandbox indisponível."}
    interp = _INTERP.get((language or "").lower().strip())
    if interp is None:
        return {"ok": False, "error": f"linguagem não suportada: {language} (use python, bash ou node)"}
    if not code or not code.strip():
        return {"ok": False, "error": "código vazio"}

    cfg = current_app.config
    timeout = cfg["SANDBOX_TIMEOUT_SECONDS"]
    max_out = cfg["SANDBOX_MAX_OUTPUT"]
    cmd = _BWRAP_BASE + ["--", *interp, code]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")
    except subprocess.TimeoutExpired:
        return {"ok": True, "timeout": True, "exit_code": None, "stdout": "",
                "stderr": f"[código interrompido após {timeout}s]"}
    except OSError as exc:
        return {"ok": False, "error": f"falha ao rodar sandbox: {exc}"}

    def _cap(t: str) -> str:
        return (t[:max_out] + "\n[... saída cortada]") if t and len(t) > max_out else (t or "")

    current_app.logger.info("SANDBOX exec %s (rc=%s)", language, r.returncode)
    return {"ok": True, "exit_code": r.returncode, "stdout": _cap(r.stdout),
            "stderr": _cap(r.stderr), "timeout": False}


EXECUTAR_CODIGO_DECL = types.FunctionDeclaration(
    name="executar_codigo",
    description=(
        "Roda um trecho de código em SANDBOX isolado (sem internet, sem acesso aos "
        "arquivos do usuário) e devolve a saída. Use para testar/validar código que "
        "você escreveu, fazer contas, processar texto, etc. Linguagens: python, bash, node. "
        "NÃO serve para mexer na máquina do usuário (para isso é executar_shell)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "linguagem": types.Schema(type=types.Type.STRING, enum=["python", "bash", "node"]),
            "codigo": types.Schema(type=types.Type.STRING),
        },
        required=["linguagem", "codigo"],
    ),
)


def executar_codigo(user_id: int, args: dict) -> dict:
    return run_sandboxed(args.get("linguagem") or "python", args.get("codigo") or "")
