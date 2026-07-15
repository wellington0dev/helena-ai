"""Auth: registro por nome+email+senha, login por email com fallback pro
username legado (contas antigas sem email, criadas antes desta mudança).
"""
from app.extensions import db
from app.models import User


def _register(client, name="Ana", email="ana@example.com", password="segredo123"):
    return client.post("/auth/register", json={"name": name, "email": email, "password": password})


def test_register_requires_name_email_password(client):
    assert client.post("/auth/register", json={"email": "a@b.com", "password": "123456"}).status_code == 400
    assert client.post("/auth/register", json={"name": "A", "password": "123456"}).status_code == 400
    assert client.post("/auth/register", json={"name": "A", "email": "a@b.com"}).status_code == 400


def test_register_rejects_short_password(client):
    r = client.post("/auth/register", json={"name": "A", "email": "a@b.com", "password": "123"})
    assert r.status_code == 400


def test_register_rejects_invalid_email(client):
    r = client.post("/auth/register", json={"name": "A", "email": "nao-e-email", "password": "123456"})
    assert r.status_code == 400


def test_register_creates_account(client):
    r = _register(client)
    assert r.status_code == 201
    data = r.get_json()
    assert data["access_token"]
    user = data["user"]
    assert user["name"] == "Ana"
    assert user["email"] == "ana@example.com"
    assert "username" not in user


def test_register_ignores_username_field(client, app):
    r = client.post("/auth/register", json={
        "name": "Bea", "email": "bea@example.com", "password": "segredo123", "username": "algo",
    })
    assert r.status_code == 201
    assert r.get_json()["user"]["name"] == "Bea"
    with app.app_context():
        u = db.session.query(User).filter_by(email="bea@example.com").first()
        assert u.username != "algo"  # gerado internamente, não veio do body


def test_register_duplicate_email_conflict(client):
    assert _register(client, email="dup@example.com").status_code == 201
    r = _register(client, name="Outra", email="dup@example.com")
    assert r.status_code == 409


def test_register_generates_distinct_usernames_on_local_part_collision(client, app):
    _register(client, name="Joao A", email="joao@a.com")
    _register(client, name="Joao B", email="joao@b.com")
    with app.app_context():
        usernames = [
            u.username for u in db.session.query(User).filter(User.email.in_(["joao@a.com", "joao@b.com"]))
        ]
    assert len(usernames) == 2
    assert len(set(usernames)) == 2
    assert all(u.startswith("joao") for u in usernames)


def test_login_by_email_success(client):
    _register(client, email="login@example.com", password="segredo123")
    r = client.post("/auth/login", json={"email": "login@example.com", "password": "segredo123"})
    assert r.status_code == 200
    assert r.get_json()["access_token"]


def test_login_by_legacy_username_fallback(client, make_user):
    make_user(username="legado")  # senha "pw" fixada na fixture, sem email
    r = client.post("/auth/login", json={"email": "legado", "password": "pw"})
    assert r.status_code == 200


def test_login_wrong_password(client):
    _register(client, email="wrong@example.com", password="segredo123")
    r = client.post("/auth/login", json={"email": "wrong@example.com", "password": "errada"})
    assert r.status_code == 401


def test_login_unknown_identifier(client):
    r = client.post("/auth/login", json={"email": "ninguem@example.com", "password": "segredo123"})
    assert r.status_code == 401


def test_login_email_case_insensitive(client):
    _register(client, email="Joao@Example.com", password="segredo123")
    r = client.post("/auth/login", json={"email": "joao@example.com", "password": "segredo123"})
    assert r.status_code == 200


def test_account_name_not_overwritten_by_preferred_name(client):
    r = _register(client, name="Ana", email="ana2@example.com")
    token = r.get_json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/account/name", json={"name": "Aninha"}, headers=headers)
    me = client.get("/account/me", headers=headers)
    assert me.get_json()["user"]["name"] == "Ana"


def test_account_basic_info_updates_name_and_email(client):
    r = _register(client, name="Carlos", email="carlos@example.com")
    token = r.get_json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    upd = client.put("/account/basic-info", json={"name": "Carlinhos", "email": "carlinhos@example.com"}, headers=headers)
    assert upd.status_code == 200
    me = client.get("/account/me", headers=headers)
    data = me.get_json()["user"]
    assert data["name"] == "Carlinhos"
    assert data["email"] == "carlinhos@example.com"


def test_account_basic_info_email_conflict(client):
    r1 = _register(client, name="A", email="a3@example.com")
    r2 = _register(client, name="B", email="b3@example.com")
    token2 = r2.get_json()["access_token"]
    headers2 = {"Authorization": f"Bearer {token2}"}
    r = client.put("/account/basic-info", json={"email": "a3@example.com"}, headers=headers2)
    assert r.status_code == 409
