#!/usr/bin/env python3
"""Sessão de prompt rica pros REPLs interativos (`helena chat`, `helena
goal`): multiline (Enter envia, Alt+Enter quebra linha), histórico
persistente com seta pra cima, autocompletar de comandos `/algo` e
sugestão fantasma baseada no histórico, e colapso automático de colados
enormes (tipo `[Texto colado #1 +240 linhas]`) — o texto completo é
devolvido pra quem chama, o placeholder é só uma economia visual no
terminal.

Não é stdlib-only (usa prompt_toolkit) — por isso, igual a chat_cli.py, é
importado sob demanda por quem precisa, nunca pelo cli.py na largada.
Cai pro input() puro quando stdin/stdout não são TTY (pipe, redirecionamento,
automação) — mesmo critério de cli_select.is_interactive()."""
from __future__ import annotations

from pathlib import Path

from cli_select import is_interactive

# acima disso, um colado vira placeholder em vez de poluir o terminal
COLLAPSE_LINES = 12
COLLAPSE_CHARS = 800


def _collapse_paste(data: str, n_existing: int) -> tuple[str, str | None]:
    """Decide se um bloco colado deve virar placeholder. Devolve
    (texto_a_inserir_no_buffer, placeholder_ou_None). Função pura — sem
    prompt_toolkit — pra dar pra testar sem terminal de verdade."""
    if not data:
        return data, None
    n_lines = data.count("\n") + 1
    if n_lines <= COLLAPSE_LINES and len(data) <= COLLAPSE_CHARS:
        return data, None
    placeholder = f"[Texto colado #{n_existing + 1} +{n_lines} linhas]"
    return placeholder, placeholder


def ask(
    prompt_label: str,
    data_dir: Path,
    history_name: str,
    slash_commands: list[str] | None = None,
    bottom_toolbar: str | None = None,
) -> tuple[str, dict[str, str]] | None:
    """Lê uma mensagem do usuário. Devolve (texto_final, colados) — texto já
    com os placeholders de colagem EXPANDIDOS de volta pro conteúdo real
    (quem chama sempre recebe o texto completo; `colados` é só pra um
    comando tipo `/colado N` mostrar o que tava por trás de um placeholder).
    Devolve None em Ctrl+C/Ctrl+D (cancelado)."""
    if not is_interactive():
        try:
            text = input(f"{prompt_label}> ")
        except (EOFError, KeyboardInterrupt):
            return None
        return text, {}

    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.styles import Style

    data_dir.mkdir(parents=True, exist_ok=True)
    pastes: dict[str, str] = {}

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _insert_newline(event) -> None:
        event.current_buffer.insert_text("\n")

    @bindings.add(Keys.BracketedPaste)
    def _handle_paste(event) -> None:
        to_insert, placeholder = _collapse_paste(event.data, len(pastes))
        if placeholder:
            pastes[placeholder] = event.data
        event.current_buffer.insert_text(to_insert)

    completer = None
    if slash_commands:
        class _SlashCompleter(Completer):
            def get_completions(self, document, complete_event):
                word = document.text_before_cursor
                if not word.startswith("/"):
                    return
                for cmd in slash_commands:
                    if cmd.startswith(word):
                        yield Completion(cmd, start_position=-len(word))

        completer = _SlashCompleter()

    style = Style.from_dict({"prompt": "bold", "bottom-toolbar": "fg:#888888"})
    session = PromptSession(
        history=FileHistory(str(data_dir / history_name)),
        auto_suggest=AutoSuggestFromHistory(),
        completer=completer,
        key_bindings=bindings,
        style=style,
        bottom_toolbar=(lambda: bottom_toolbar) if bottom_toolbar else None,
    )
    try:
        raw = session.prompt([("class:prompt", f"{prompt_label}› ")])
    except (EOFError, KeyboardInterrupt):
        return None

    text = raw
    for placeholder, original in pastes.items():
        text = text.replace(placeholder, original)
    return text, pastes
