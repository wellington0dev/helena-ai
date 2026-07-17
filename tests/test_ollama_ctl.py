"""ollama_ctl.py: controle do Ollama (compartilhado cli.py/blueprint web) —
subprocess/rede sempre monkeypatchados, nunca toca um Ollama de verdade."""
import subprocess
import urllib.error

import ollama_ctl


class _FakeResp:
    def __init__(self, status=200, body=b"{}"):
        self.status = status
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_reachable_true(monkeypatch):
    monkeypatch.setattr(ollama_ctl.urllib.request, "urlopen", lambda *a, **k: _FakeResp(200))
    assert ollama_ctl.reachable("http://x") is True


def test_reachable_false_em_erro(monkeypatch):
    def _boom(*a, **k):
        raise OSError("recusado")
    monkeypatch.setattr(ollama_ctl.urllib.request, "urlopen", _boom)
    assert ollama_ctl.reachable("http://x") is False


def test_list_installed_sem_ollama(monkeypatch):
    monkeypatch.setattr(ollama_ctl.shutil, "which", lambda name: None)
    assert ollama_ctl.list_installed() == set()


def test_list_installed_parseia_saida(monkeypatch):
    monkeypatch.setattr(ollama_ctl.shutil, "which", lambda name: "/usr/bin/ollama")
    out = "NAME             ID       SIZE\nqwen2.5:1.5b     abc123   1.0 GB\nllama3.1:8b      def456   4.9 GB\n"
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr=""),
    )
    assert ollama_ctl.list_installed() == {"qwen2.5:1.5b", "llama3.1:8b"}


def test_smoke_test_ok(monkeypatch):
    monkeypatch.setattr(
        ollama_ctl.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(200, b'{"response": "oi!"}'),
    )
    ok, detail = ollama_ctl.smoke_test("http://x", "modelo")
    assert ok is True and detail == ""


def test_smoke_test_resposta_vazia(monkeypatch):
    monkeypatch.setattr(
        ollama_ctl.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(200, b'{"response": ""}'),
    )
    ok, detail = ollama_ctl.smoke_test("http://x", "modelo")
    assert ok is False and "vazia" in detail


def test_smoke_test_http_error_extrai_mensagem(monkeypatch):
    def _boom(*a, **k):
        raise urllib.error.HTTPError(
            "http://x", 400, "bad", {}, __import__("io").BytesIO(b'{"error": "modelo nao suporta tools"}')
        )
    monkeypatch.setattr(ollama_ctl.urllib.request, "urlopen", _boom)
    ok, detail = ollama_ctl.smoke_test("http://x", "modelo")
    assert ok is False
    assert "nao suporta tools" in detail


def test_pull_sucesso(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0),
    )
    assert ollama_ctl.pull("qwen2.5:1.5b") is True


def test_pull_falha(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1),
    )
    assert ollama_ctl.pull("qwen2.5:1.5b") is False


def test_rm_falha_devolve_stderr(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="não encontrado"),
    )
    ok, msg = ollama_ctl.rm("inexistente")
    assert ok is False
    assert "não encontrado" in msg


def test_ensure_daemon_ja_alcancavel(monkeypatch):
    monkeypatch.setattr(ollama_ctl, "reachable", lambda host, timeout=2.0: True)
    assert ollama_ctl.ensure_daemon("http://x") is True


def test_install_ja_instalado_nao_chama_subprocess(monkeypatch):
    monkeypatch.setattr(ollama_ctl.shutil, "which", lambda name: "/usr/bin/ollama")
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a) or None)
    ok, msg = ollama_ctl.install()
    assert ok is True and msg == "" and not called
