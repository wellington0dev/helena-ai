"""env_file.py: leitura/escrita do .env — usado tanto pelo cli.py quanto
pelo blueprint web (app/blueprints/settings.py). Puro, sem Flask."""
from pathlib import Path

import env_file


def test_read_env_values_ignora_comentarios_e_vazio(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n# B=2\n\nC=hello world\n")
    vals = env_file.read_env_values(p)
    assert vals == {"A": "1", "C": "hello world"}


def test_read_env_values_arquivo_inexistente(tmp_path):
    assert env_file.read_env_values(tmp_path / "nope.env") == {}


def test_set_env_values_cria_a_partir_do_example(tmp_path):
    example = tmp_path / ".env.example"
    example.write_text("A=\nB=default\n")
    p = tmp_path / ".env"
    env_file.set_env_values(p, {"A": "1"}, example_path=example)
    vals = env_file.read_env_values(p)
    assert vals["A"] == "1"
    assert vals["B"] == "default"


def test_set_env_values_preserva_ordem_e_comentarios(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comentário\nA=1\nB=2\n")
    env_file.set_env_values(p, {"A": "novo"})
    text = p.read_text()
    lines = text.splitlines()
    assert lines[0] == "# comentário"
    assert "A=novo" in lines
    assert "B=2" in lines


def test_set_env_values_substitui_placeholder_comentado(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# CHAVE=\n")
    env_file.set_env_values(p, {"CHAVE": "valor"})
    assert env_file.read_env_values(p) == {"CHAVE": "valor"}


def test_set_env_values_insere_chave_nova_no_fim(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    env_file.set_env_values(p, {"NOVA": "x"})
    vals = env_file.read_env_values(p)
    assert vals == {"A": "1", "NOVA": "x"}


def test_mask_plain():
    assert env_file.mask_plain("") == ""
    assert env_file.mask_plain("abcd") == "••••"
    assert env_file.mask_plain("AIzaSyABCDEFGHIJKL") == "AIza••••••IJKL"
