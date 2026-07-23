"""cli_prompt._collapse_paste: decide se um bloco colado vira placeholder.
Função pura — sem terminal/prompt_toolkit envolvido."""
from cli_prompt import COLLAPSE_CHARS, COLLAPSE_LINES, _collapse_paste


def test_colado_pequeno_nao_colapsa():
    data = "linha1\nlinha2\n"
    text, placeholder = _collapse_paste(data, 0)
    assert text == data
    assert placeholder is None


def test_colado_vazio():
    text, placeholder = _collapse_paste("", 0)
    assert text == ""
    assert placeholder is None


def test_colado_muitas_linhas_colapsa():
    data = "\n".join(f"linha{i}" for i in range(COLLAPSE_LINES + 5))
    text, placeholder = _collapse_paste(data, 0)
    assert placeholder is not None
    assert text == placeholder
    assert "#1" in placeholder
    assert "linhas" in placeholder


def test_colado_muitos_caracteres_colapsa_mesmo_com_poucas_linhas():
    data = "x" * (COLLAPSE_CHARS + 100)
    text, placeholder = _collapse_paste(data, 0)
    assert placeholder is not None


def test_placeholder_incrementa_com_n_existing():
    data = "\n".join(f"linha{i}" for i in range(COLLAPSE_LINES + 5))
    _, placeholder0 = _collapse_paste(data, 0)
    _, placeholder1 = _collapse_paste(data, 1)
    assert "#1" in placeholder0
    assert "#2" in placeholder1
