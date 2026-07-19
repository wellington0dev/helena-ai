#!/usr/bin/env python3
"""Leitura/escrita do `.env` — stdlib-only, compartilhado entre `cli.py` e o
blueprint web `app/blueprints/settings.py` (fonte única de verdade: os dois
NÃO podem ter implementações paralelas que divergem com o tempo)."""
from __future__ import annotations

import re
from pathlib import Path

def read_env_values(path: Path) -> dict[str, str]:
    """Lê os pares KEY=VALUE ativos (não comentados) de um arquivo .env."""
    vals: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)\s*=(.*)$", line)
            if m:
                vals[m.group(1)] = m.group(2).strip()
    return vals


def set_env_values(path: Path, updates: dict[str, str], *, example_path: Path | None = None) -> None:
    """Atualiza (ou insere) chaves no .env preservando comentários e ordem.
    Substitui inclusive linhas comentadas do tipo `# KEY=` (placeholders).
    Se o arquivo ainda não existir e `example_path` for dado (e existir), o
    arquivo nasce a partir dele."""
    if not path.exists() and example_path is not None and example_path.exists():
        path.write_text(example_path.read_text())
    lines = path.read_text().splitlines() if path.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        m = re.match(r"^\s*#?\s*([A-Z_][A-Z0-9_]*)\s*=", line)
        if m and m.group(1) in updates:
            key = m.group(1)
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    path.write_text("\n".join(out) + "\n")


def mask_plain(value: str) -> str:
    """Mascara um segredo pra exibição — sem cor (uso tanto no terminal
    quanto em JSON pra web). Vazio vira string vazia (quem chama decide como
    rotular "não configurado")."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 6}{value[-4:]}"
