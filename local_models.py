#!/usr/bin/env python3
"""Catálogo de modelos locais (Ollama) + detecção de hardware, para recomendar
o tamanho de modelo adequado à máquina do usuário no `helena setup`/`helena
models`. Só stdlib + psutil (já é dependência do projeto) — nada de Flask,
importável direto pelo cli.py sem subir o app inteiro.

Best-effort e aproximado de propósito: RAM/CPU vêm do psutil; VRAM de GPU só
é detectada se `nvidia-smi`/`rocm-smi` estiverem no PATH (NVIDIA/AMD); sem
GPU detectável, assume CPU-only.
"""
import os
import shutil
import subprocess

# Catálogo curado: só modelos com suporte a TOOL-CALLING confirmado no Ollama
# — um modelo sem essa capability responde HTTP 400 ao tentar usar tools (não
# é degradação silenciosa), então a curadoria é estrita de propósito. Ordenado
# por "potência" crescente. `est_gb` é o tamanho aproximado do download em Q4
# (quantização default do `ollama pull`) — serve só pra estimar se cabe no
# hardware, não é exato (varia por arquitetura). Essa lista evolui junto com o
# Ollama; conferir o catálogo atual em https://ollama.com/search?c=tools.
CATALOG = [
    {"name": "qwen2.5:0.5b", "label": "Qwen 2.5", "params_b": 0.5, "est_gb": 0.4},
    {"name": "qwen2.5:1.5b", "label": "Qwen 2.5", "params_b": 1.5, "est_gb": 1.0},
    {"name": "qwen2.5:3b", "label": "Qwen 2.5", "params_b": 3, "est_gb": 1.9},
    {"name": "qwen2.5:7b", "label": "Qwen 2.5", "params_b": 7, "est_gb": 4.7},
    {"name": "mistral:7b", "label": "Mistral", "params_b": 7, "est_gb": 4.1},
    {"name": "llama3.1:8b", "label": "Llama 3.1", "params_b": 8, "est_gb": 4.9},
    {"name": "mistral-nemo:12b", "label": "Mistral Nemo", "params_b": 12, "est_gb": 7.1},
    {"name": "qwen2.5:14b", "label": "Qwen 2.5", "params_b": 14, "est_gb": 9.0},
    {"name": "mistral-small:22b", "label": "Mistral Small", "params_b": 22, "est_gb": 13.0},
    {"name": "qwen2.5:32b", "label": "Qwen 2.5", "params_b": 32, "est_gb": 20.0},
    {"name": "llama3.1:70b", "label": "Llama 3.1", "params_b": 70, "est_gb": 43.0},
    {"name": "qwen2.5:72b", "label": "Qwen 2.5", "params_b": 72, "est_gb": 47.0},
]

# overhead de contexto/runtime somado ao tamanho do modelo pra estimar a
# necessidade real de RAM/VRAM (janela de contexto, KV cache etc.)
_RUNTIME_OVERHEAD_GB = 1.5

# acima disso, CPU pura é impraticável (lenta demais pra uso interativo)
# mesmo cabendo em RAM — só GPU torna esse tamanho viável
_CPU_ONLY_PARAM_CEILING_B = 13


def _ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:  # noqa: BLE001 — chute conservador é melhor que travar
        return 8.0


def _cpu_count() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=True) or os.cpu_count() or 4
    except Exception:  # noqa: BLE001
        return os.cpu_count() or 4


def _run(cmd: list[str]) -> str | None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _nvidia_vram_gb() -> float | None:
    if not shutil.which("nvidia-smi"):
        return None
    out = _run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"])
    if not out:
        return None
    try:
        mib = float(out.strip().splitlines()[0].strip())
        return round(mib / 1024, 1)
    except (ValueError, IndexError):
        return None


def _rocm_vram_gb() -> float | None:
    if not shutil.which("rocm-smi"):
        return None
    out = _run(["rocm-smi", "--showmeminfo", "vram", "--json"])
    if not out:
        return None
    try:
        import json
        data = json.loads(out)
        for card in data.values():
            total = card.get("VRAM Total Memory (B)")
            if total:
                return round(float(total) / (1024 ** 3), 1)
    except (ValueError, AttributeError, TypeError):
        return None
    return None


def detect_hardware() -> dict:
    """RAM total (GB), contagem de CPU e VRAM de GPU (GB, best-effort — None
    se não detectar nenhuma GPU, assume CPU-only)."""
    return {
        "ram_gb": _ram_gb(),
        "cpu_count": _cpu_count(),
        "gpu_vram_gb": _nvidia_vram_gb() or _rocm_vram_gb(),
    }


def usable_capacity_gb(hw: dict) -> float:
    """Quanto dá pra usar sem sufocar a máquina. Com GPU, é a VRAM (o modelo
    roda nela, não compete com o resto). Sem GPU, uma fração da RAM — o SO e
    a própria Helena (que roda na mesma máquina) precisam de folga."""
    vram = hw.get("gpu_vram_gb")
    if vram:
        return vram
    ram = hw.get("ram_gb") or 8.0
    return max(0.0, min(ram - 4.0, ram * 0.6))


def rate_model(model: dict, hw: dict) -> str:
    """'green' (adequado) | 'yellow' (roda, mas custa desempenho) | 'red'
    (não vai rodar ou vai rodar mal). Best-effort, não é ciência exata."""
    need = model["est_gb"] + _RUNTIME_OVERHEAD_GB
    cap = usable_capacity_gb(hw)
    has_gpu = bool(hw.get("gpu_vram_gb"))
    if not has_gpu and model["params_b"] > _CPU_ONLY_PARAM_CEILING_B:
        # CPU pura nesse tamanho é impraticável mesmo cabendo em RAM
        return "red" if need > cap else "yellow"
    if need <= 0.7 * cap:
        return "green"
    if need <= cap:
        return "yellow"
    return "red"


def find_model(name: str) -> dict | None:
    return next((m for m in CATALOG if m["name"] == name), None)
