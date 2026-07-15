"""Tarefa de navegação/desktop autônoma (job type=desktop_task):
- disparo exige controle absoluto (age de verdade na máquina do usuário);
- coordenadas miradas no print REDUZIDO precisam mapear pro pixel REAL da tela;
- o detector de empacamento do loop precisa tolerar repetições legítimas de
  desktop (rolar 2x) sem quebrar o job no meio, mas ainda parar um loop de verdade."""
from app.agent import desktop, job_tools
from app.agent.gemini import run_agent


def test_iniciar_tarefa_exige_fullcontrol(app, make_user):
    uid = make_user("normal", is_principal=True)  # principal mas sem fullcontrol
    with app.app_context():
        r = job_tools.run_background_job(
            uid, {"type": "desktop_task", "payload": {"title": "t", "task": "x"}}
        )
        assert r["ok"] is False
        assert "controle absoluto" in r["error"]


def test_iniciar_tarefa_ok_com_fullcontrol(app, make_user):
    uid = make_user("full", is_principal=True, shell_full_control=True)
    with app.app_context():
        r = job_tools.run_background_job(
            uid, {"type": "desktop_task", "payload": {"title": "t", "task": "x"}}
        )
        assert r["ok"] is True and r["job_id"]


def test_tipo_invalido_ainda_recusado(app, make_user):
    uid = make_user("full", shell_full_control=True)
    with app.app_context():
        r = job_tools.run_background_job(uid, {"type": "hack", "payload": {"x": 1}})
        assert r["ok"] is False


def test_coordenada_mapeia_print_reduzido_para_tela_real(monkeypatch):
    # tela real 1920x1080, print enviado ao modelo reduzido pra 960x540 (metade)
    desktop._screen_wh = (1920, 1080)
    desktop._last_shot_wh = (960, 540)
    # o modelo mirou no CENTRO do print que viu (960x540) → deve virar o centro real
    assert desktop.report_to_real(480, 270) == (960, 540)
    # canto (0,0) continua (0,0)
    assert desktop.report_to_real(0, 0) == (0, 0)


def test_coordenada_sem_downscale_e_identidade():
    desktop._screen_wh = (1280, 720)
    desktop._last_shot_wh = (1280, 720)
    assert desktop.report_to_real(300, 200) == (300, 200)


class _Call:
    def __init__(self, name, args):
        self.name = name
        self.args = args


class _Content:
    def __init__(self):
        self.parts = []


class _Candidate:
    def __init__(self):
        self.content = _Content()


class _Resp:
    def __init__(self, calls=None, text=""):
        self.function_calls = calls or []
        self.text = text
        self.candidates = [_Candidate()]


class _FakeModels:
    def __init__(self, script):
        self._script = script
        self.calls_made = 0

    def generate_content(self, **_):
        self.calls_made += 1
        return self._script[min(self.calls_made - 1, len(self._script) - 1)]


class _FakeClient:
    def __init__(self, script):
        self.models = _FakeModels(script)


def _repeated_call_resp():
    return _Resp(calls=[_Call("rolar", {"quantidade": -3})])


def test_stuck_detector_default_quebra_na_1a_repeticao(app, monkeypatch):
    # a 3ª entrada (o pedido de fecho/wrap-up) devolve um resumo de verdade —
    # prova que o loop pede e usa o fecho, não só cai no fallback genérico
    script = [
        _repeated_call_resp(), _repeated_call_resp(),
        _Resp(text="resumo do que rolou até travar"),
    ]
    client = _FakeClient(script)
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: client)
    with app.app_context():
        text, executed = run_agent(
            user_id=1, api_key="k", model="m", max_iters=10,
            system_instruction="s", initial_contents=[],
            dispatch=lambda name, args, uid: {"ok": True},
        )
    assert executed is True
    # quebrou na 2ª chamada idêntica + 1 chamada extra de fecho (wrap-up)
    assert client.models.calls_made == 3
    assert text == "resumo do que rolou até travar"  # usou o fecho, não perdeu o progresso


def test_stuck_detector_desktop_tolera_repeticoes_legitimas(app, monkeypatch):
    script = [
        _repeated_call_resp(), _repeated_call_resp(), _repeated_call_resp(),
        _Resp(text="entrega final"),
    ]
    client = _FakeClient(script)
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: client)
    with app.app_context():
        text, executed = run_agent(
            user_id=1, api_key="k", model="m", max_iters=10,
            system_instruction="s", initial_contents=[],
            dispatch=lambda name, args, uid: {"ok": True},
            stuck_repeat_limit=3,
        )
    assert executed is True
    assert text == "entrega final"
    assert client.models.calls_made == 4  # não quebrou cedo, chegou até a entrega


def test_stuck_detector_desktop_ainda_quebra_eventualmente(app, monkeypatch):
    script = [_repeated_call_resp()] * 5
    client = _FakeClient(script)
    monkeypatch.setattr("app.agent.gemini.get_client", lambda key: client)
    with app.app_context():
        text, _ = run_agent(
            user_id=1, api_key="k", model="m", max_iters=10,
            system_instruction="s", initial_contents=[],
            dispatch=lambda name, args, uid: {"ok": True},
            stuck_repeat_limit=3,
        )
    # 1ª + 3 repetições = quebra na 4ª, + 1 chamada extra de fecho (wrap-up)
    assert client.models.calls_made == 5
    assert text  # nunca perde o progresso silenciosamente
