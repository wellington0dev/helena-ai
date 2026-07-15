"""Sandbox (bubblewrap) — o valor está no ISOLAMENTO, então testamos NEGATIVAMENTE:
sem rede, sem acesso ao FS do host (.env/.ssh), e fork-bomb morre no timeout.

Se `bwrap` não estiver instalado, pulamos — o isolamento não pode ser garantido."""
import shutil

import pytest

from app.agent.sandbox import run_sandboxed

pytestmark = pytest.mark.skipif(
    not shutil.which("bwrap"), reason="bubblewrap (bwrap) não instalado"
)


def test_positivo_roda_e_devolve_saida(app):
    with app.app_context():
        r = run_sandboxed("python", "print(2 + 2)")
    assert r["ok"] and r["exit_code"] == 0
    assert r["stdout"].strip() == "4"


def test_sem_rede(app):
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
        "    print('CONECTOU')\n"
        "except OSError as e:\n"
        "    print('SEM_REDE', e)\n"
    )
    with app.app_context():
        r = run_sandboxed("python", code)
    assert "CONECTOU" not in r["stdout"]
    assert "SEM_REDE" in r["stdout"]


def test_sem_acesso_ao_home_do_host(app):
    # tenta ler qualquer coisa sensível do host; o FS do host não está montado
    code = (
        "import glob, os\n"
        "achados = glob.glob('/home/*/.env') + glob.glob('/home/*/.ssh/*') "
        "+ glob.glob('/root/*')\n"
        "print('ACHOU' if achados else 'NADA', achados[:3])\n"
    )
    with app.app_context():
        r = run_sandboxed("python", code)
    assert "NADA" in r["stdout"]
    assert "ACHOU" not in r["stdout"]


def test_env_do_projeto_inacessivel(app):
    # o .env real do server tem a chave da API — não pode vazar pro sandbox
    with app.app_context():
        r = run_sandboxed(
            "bash", "cat /home/weber/Projects/h1/server/.env 2>&1 || echo FALHOU"
        )
    assert "GEMINI_API_KEY" not in r["stdout"]
    assert "FALHOU" in r["stdout"] or r["exit_code"] != 0


def test_fork_bomb_morre_no_timeout(app):
    # loop infinito: precisa ser morto pelo timeout, não travar o processo
    with app.app_context():
        r = run_sandboxed("python", "while True: pass")
    assert r["ok"] and r["timeout"] is True


def test_linguagem_invalida(app):
    with app.app_context():
        r = run_sandboxed("cobol", "DISPLAY 'oi'")
    assert r["ok"] is False and "linguagem" in r["error"]
