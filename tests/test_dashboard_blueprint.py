"""Blueprint /dashboard: gate de permissão (principal+, não qualquer logado —
expõe atividade de outros usuários e processos do SO) + shape da resposta."""
from app.extensions import db
from app.models import Job, User


def test_overview_exige_auth(client):
    r = client.get("/dashboard/overview")
    assert r.status_code == 401


def test_overview_normal_user_negado(client, make_user, auth):
    uid = make_user("normal")
    r = client.get("/dashboard/overview", headers=auth(uid))
    assert r.status_code == 403


def test_overview_principal_ve_o_painel(client, make_user, auth):
    uid = make_user("p", is_principal=True)
    r = client.get("/dashboard/overview", headers=auth(uid))
    assert r.status_code == 200
    data = r.get_json()
    assert "users" in data and "jobs" in data and "system" in data and "processes" in data


def test_overview_lista_usuarios_e_jobs_ativos(app, client, make_user, auth):
    uid = make_user("p", is_principal=True)
    other = make_user("other")
    with app.app_context():
        db.session.add(Job(user_id=other, type="research", payload={"title": "pesquisa teste"}, status="running"))
        db.session.add(Job(user_id=other, type="plan", payload={"title": "plano antigo"}, status="done"))
        db.session.commit()

    r = client.get("/dashboard/overview", headers=auth(uid))
    data = r.get_json()

    user_ids = {u["id"] for u in data["users"]}
    assert uid in user_ids and other in user_ids

    other_row = next(u for u in data["users"] if u["id"] == other)
    assert other_row["active_jobs"] == 1  # só o "running" conta, não o "done"

    job_types = [j["type"] for j in data["jobs"]]
    assert "research" in job_types
    assert "plan" not in job_types  # status done não aparece (só ativos)

    research_job = next(j for j in data["jobs"] if j["type"] == "research")
    assert research_job["title"] == "pesquisa teste"


def test_overview_atualiza_last_seen_at(app, client, make_user, auth):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        assert db.session.get(User, uid).last_seen_at is None

    client.get("/dashboard/overview", headers=auth(uid))

    with app.app_context():
        assert db.session.get(User, uid).last_seen_at is not None


def test_overview_system_e_processes_tem_dados(client, make_user, auth):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    r = client.get("/dashboard/overview", headers=auth(uid))
    data = r.get_json()
    assert isinstance(data["processes"], list)
    assert len(data["processes"]) > 0  # a própria máquina de teste tem processos rodando
    assert "cpu_percent" in data["system"]
