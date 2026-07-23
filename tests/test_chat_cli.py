"""chat_cli.api_upload_media / api_send(media_url=...): requests sempre
monkeypatchado — nunca bate num servidor de verdade em teste."""
import chat_cli
from chat_cli import ChatCliError, api_send, api_upload_media


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_upload_media_faz_post_multipart_correto(monkeypatch):
    calls = []

    def _fake_post(url, headers=None, files=None, json=None, timeout=None):
        calls.append((url, headers, files, json))
        return _FakeResponse(201, {"media_url": "u/1/img.png", "media_type": "image"})

    monkeypatch.setattr(chat_cli.requests, "post", _fake_post)
    result = api_upload_media("http://x", "tok", b"bytes", "colado.png")
    assert result == {"media_url": "u/1/img.png", "media_type": "image"}
    url, headers, files, _ = calls[0]
    assert url == "http://x/media/upload"
    assert headers == {"Authorization": "Bearer tok"}
    assert files["file"][0] == "colado.png"
    assert files["file"][1] == b"bytes"


def test_upload_media_expirado(monkeypatch):
    monkeypatch.setattr(
        chat_cli.requests, "post", lambda *a, **k: _FakeResponse(401, {"error": "expirado"})
    )
    try:
        api_upload_media("http://x", "tok", b"bytes", "colado.png")
        assert False, "deveria ter levantado ChatCliError"
    except ChatCliError as e:
        assert str(e) == "__EXPIRED__"


def test_upload_media_erro_servidor(monkeypatch):
    monkeypatch.setattr(
        chat_cli.requests, "post", lambda *a, **k: _FakeResponse(400, {"error": "arquivo vazio"})
    )
    try:
        api_upload_media("http://x", "tok", b"", "colado.png")
        assert False, "deveria ter levantado ChatCliError"
    except ChatCliError as e:
        assert "arquivo vazio" in str(e)


def test_api_send_inclui_media_quando_fornecido(monkeypatch):
    calls = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(200, {"replies": []})

    monkeypatch.setattr(chat_cli.requests, "post", _fake_post)
    api_send("http://x", "tok", "olá", media_url="u/1/img.png", media_type="image")
    assert calls[0]["media_url"] == "u/1/img.png"
    assert calls[0]["media_type"] == "image"


def test_api_send_sem_media_nao_inclui_campos(monkeypatch):
    calls = []

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(200, {"replies": []})

    monkeypatch.setattr(chat_cli.requests, "post", _fake_post)
    api_send("http://x", "tok", "olá")
    assert "media_url" not in calls[0]
