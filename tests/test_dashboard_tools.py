"""Tools abrir_dashboard/fechar_dashboard: gate de permissão (fullcontrol,
mesmo tier de mouse/teclado) + subprocess sempre monkeypatchado — nunca abre
uma janela de verdade em teste."""
import app.agent.dashboard_tools as dashboard_tools


class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def setup_function():
    dashboard_tools._proc = None


def test_principal_sem_fullcontrol_nao_pode_abrir(app, make_user):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        r = dashboard_tools.abrir_dashboard(uid, {})
        assert r["ok"] is False
        assert "controle absoluto" in r["error"].lower()


def test_fullcontrol_sem_electron_instalado_da_erro_claro(app, make_user, monkeypatch):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    monkeypatch.setattr(dashboard_tools, "_electron_bin", lambda: None)
    with app.app_context():
        r = dashboard_tools.abrir_dashboard(uid, {})
        assert r["ok"] is False
        assert "npm install" in r["error"]


def test_fullcontrol_abre_com_electron_presente(app, make_user, monkeypatch, tmp_path):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    fake_electron = tmp_path / "electron"
    fake_electron.write_text("")
    monkeypatch.setattr(dashboard_tools, "_electron_bin", lambda: fake_electron)

    calls = []

    def _fake_popen(args, **kwargs):
        calls.append(args)
        return _FakeProc(alive=True)

    monkeypatch.setattr(dashboard_tools.subprocess, "Popen", _fake_popen)
    with app.app_context():
        r = dashboard_tools.abrir_dashboard(uid, {})
        assert r["ok"] is True
        assert len(calls) == 1
        assert str(fake_electron) in calls[0]
        assert "--url" in calls[0]


def test_abrir_duas_vezes_nao_reabre(app, make_user, monkeypatch, tmp_path):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    fake_electron = tmp_path / "electron"
    fake_electron.write_text("")
    monkeypatch.setattr(dashboard_tools, "_electron_bin", lambda: fake_electron)
    calls = []
    monkeypatch.setattr(
        dashboard_tools.subprocess, "Popen",
        lambda args, **kwargs: calls.append(args) or _FakeProc(alive=True),
    )
    with app.app_context():
        dashboard_tools.abrir_dashboard(uid, {})
        r2 = dashboard_tools.abrir_dashboard(uid, {})
        assert r2["ok"] is True
        assert "já está aberto" in r2["info"]
        assert len(calls) == 1  # não spawnou um segundo processo


def test_fechar_sem_nada_aberto_nao_e_erro(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        r = dashboard_tools.fechar_dashboard(uid, {})
        assert r["ok"] is True
        assert "não estava aberto" in r["info"]


def test_fechar_termina_o_processo_rastreado(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    fake = _FakeProc(alive=True)
    dashboard_tools._proc = fake
    with app.app_context():
        r = dashboard_tools.fechar_dashboard(uid, {})
        assert r["ok"] is True
        assert fake.terminated is True
        assert dashboard_tools._proc is None


def test_fechar_sem_fullcontrol_negado(app, make_user):
    uid = make_user("p", is_principal=True)
    dashboard_tools._proc = _FakeProc(alive=True)
    with app.app_context():
        r = dashboard_tools.fechar_dashboard(uid, {})
        assert r["ok"] is False
