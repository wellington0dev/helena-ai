#!/usr/bin/env python3
"""Menu de seleção interativo (setas ↑/↓ + Enter), stdlib-only.

Usado pelo cli.py e pelo chat_cli.py para trocar prompts de "digite uma opção
válida" por um menu navegável — mas sempre com fallback numérico (digitável)
quando o terminal não suporta modo raw (pipe, redirecionamento, non-tty,
Windows sem console interativo). Nunca trava: qualquer erro no modo setas cai
pro fallback."""
from __future__ import annotations

import os
import sys

IS_WIN = os.name == "nt"
_TTY = sys.stdout.isatty()


def _c(txt: str, code: str) -> str:
    return f"\033[{code}m{txt}\033[0m" if _TTY else txt


def _bold(t: str) -> str:
    return _c(t, "1")


def _dim(t: str) -> str:
    return _c(t, "2")


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def is_interactive() -> bool:
    """Versão pública de `_interactive` — usada por outros módulos de CLI
    (ex.: cli_prompt.py) que precisam do mesmo critério de fallback."""
    return _interactive()


def select_menu(prompt: str, options: list[tuple[str, str]], default: int = 0) -> str | None:
    """`options`: lista de (valor, rótulo). Devolve o valor escolhido, ou None
    se cancelado (Ctrl+C/Esc/EOF)."""
    if not options:
        return None
    if len(options) == 1:
        return options[0][0]
    default = max(0, min(default, len(options) - 1))
    if _interactive():
        try:
            return _select_arrows(prompt, options, default)
        except Exception:
            pass  # terminal não suporta modo raw → cai pro fallback numérico
    return _select_numeric(prompt, options, default)


def confirm(prompt: str, default: bool = True) -> bool:
    val = select_menu(prompt, [("s", "Sim"), ("n", "Não")], default=0 if default else 1)
    return default if val is None else val == "s"


# --------------------------------------------------------------------------- #
# fallback: numerado, digitável (funciona em qualquer terminal/pipe)
# --------------------------------------------------------------------------- #

def _select_numeric(prompt: str, options: list[tuple[str, str]], default: int) -> str | None:
    print(_bold(prompt))
    for i, (_, label) in enumerate(options, 1):
        marker = "*" if i - 1 == default else " "
        print(f"  {marker} {i}) {label}")
    while True:
        try:
            ans = input(_dim(f"escolha [1-{len(options)}] (Enter = {default + 1}): ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        if not ans:
            return options[default][0]
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1][0]
        print("opção inválida.")


# --------------------------------------------------------------------------- #
# modo setas: redesenha o menu no lugar via ANSI cursor-up + clear-to-end
# --------------------------------------------------------------------------- #

def _render(options: list[tuple[str, str]], idx: int, first: bool) -> None:
    if not first:
        sys.stdout.write(f"\033[{len(options)}A")  # sobe o cursor pro topo do menu
    sys.stdout.write("\033[J")  # limpa daqui até o fim da tela
    for i, (_, label) in enumerate(options):
        marker = "❯" if i == idx else " "
        line = f" {marker} {label}"
        sys.stdout.write((_bold(line) if i == idx else line) + "\n")
    sys.stdout.flush()


def _select_arrows(prompt: str, options: list[tuple[str, str]], default: int) -> str | None:
    if IS_WIN:
        return _select_arrows_win(prompt, options, default)
    return _select_arrows_posix(prompt, options, default)


def _select_arrows_posix(prompt: str, options: list[tuple[str, str]], default: int) -> str | None:
    import termios
    import tty

    idx = default
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print(_bold(prompt))
    print(_dim("(setas + Enter, ou digite o número)"))
    try:
        tty.setcbreak(fd)
        _render(options, idx, first=True)
        while True:
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                ch2 = sys.stdin.read(1)
                if ch2 != "[":
                    return None  # Esc puro
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    idx = (idx - 1) % len(options)
                    _render(options, idx, first=False)
                elif ch3 == "B":
                    idx = (idx + 1) % len(options)
                    _render(options, idx, first=False)
            elif ch in ("\r", "\n"):
                return options[idx][0]
            elif ch == "\x03":
                raise KeyboardInterrupt
            elif ch.isdigit() and 1 <= int(ch) <= len(options):
                idx = int(ch) - 1
                _render(options, idx, first=False)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _select_arrows_win(prompt: str, options: list[tuple[str, str]], default: int) -> str | None:
    import msvcrt

    idx = default
    print(_bold(prompt))
    print(_dim("(setas + Enter, ou digite o número)"))
    _render(options, idx, first=True)
    while True:
        ch = msvcrt.getch()
        if ch in (b"\xe0", b"\x00"):  # prefixo de tecla especial (setas, F-keys)
            ch2 = msvcrt.getch()
            if ch2 == b"H":
                idx = (idx - 1) % len(options)
                _render(options, idx, first=False)
            elif ch2 == b"P":
                idx = (idx + 1) % len(options)
                _render(options, idx, first=False)
        elif ch in (b"\r", b"\n"):
            return options[idx][0]
        elif ch == b"\x03":
            raise KeyboardInterrupt
        elif ch == b"\x1b":
            return None
        elif ch.isdigit() and 1 <= int(ch) <= len(options):
            idx = int(ch) - 1
            _render(options, idx, first=False)
