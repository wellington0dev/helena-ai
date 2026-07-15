"""Consolidação de memória: NUNCA poda notas se a chamada falhar/vier inválida
ou perder chaves do perfil (é uma reescrita LLM — perda de dados é o risco)."""
import app.agent.memory as memory
from app.extensions import db
from app.models import AiNote, UserProfile


class _Resp:
    def __init__(self, text):
        self.text = text


class _Client:
    def __init__(self, text_or_exc):
        self._x = text_or_exc

    class _Models:
        def __init__(self, x):
            self._x = x

        def generate_content(self, **_):
            if isinstance(self._x, Exception):
                raise self._x
            return _Resp(self._x)

    @property
    def models(self):
        return self._Models(self._x)


def _stub(monkeypatch, text_or_exc):
    monkeypatch.setattr(memory, "get_client", lambda key: _Client(text_or_exc))


def _seed(app, uid, n=35, profile=None):
    with app.app_context():
        for i in range(n):
            db.session.add(AiNote(user_id=uid, content=f"nota {i}", category="fato"))
        if profile is not None:
            db.session.add(UserProfile(user_id=uid, profile=profile))
        db.session.commit()


def test_does_not_prune_on_llm_failure(app, make_user, monkeypatch):
    uid = make_user("m")
    _seed(app, uid, 35)
    _stub(monkeypatch, RuntimeError("boom"))
    with app.app_context():
        memory.maybe_consolidate(uid, "k", "model")
        assert db.session.query(AiNote).filter_by(user_id=uid).count() == 35  # nada podado


def test_does_not_prune_on_empty_json(app, make_user, monkeypatch):
    uid = make_user("m")
    _seed(app, uid, 35)
    _stub(monkeypatch, "{}")
    with app.app_context():
        memory.maybe_consolidate(uid, "k", "model")
        assert db.session.query(AiNote).filter_by(user_id=uid).count() == 35


def test_does_not_prune_if_profile_keys_lost(app, make_user, monkeypatch):
    uid = make_user("m")
    _seed(app, uid, 35, profile={"nome_preferido": "Well"})
    _stub(monkeypatch, '{"gostos": ["x"]}')  # perdeu nome_preferido
    with app.app_context():
        memory.maybe_consolidate(uid, "k", "model")
        assert db.session.query(AiNote).filter_by(user_id=uid).count() == 35


def test_consolidates_when_valid(app, make_user, monkeypatch):
    uid = make_user("m")
    _seed(app, uid, 35, profile={"nome_preferido": "Well"})
    _stub(monkeypatch, '{"nome_preferido": "Well", "gostos": ["anime"]}')
    with app.app_context():
        memory.maybe_consolidate(uid, "k", "model")
        # podou as antigas até sobrar ~KEEP recentes
        keep = app.config["MEMORY_NOTES_KEEP"]
        assert db.session.query(AiNote).filter_by(user_id=uid).count() == keep
        assert db.session.get(UserProfile, uid).profile["gostos"] == ["anime"]
