"""CRUD de comandos salvos: nome único, escopo por dono, procedência 'user'."""


def test_command_crud_ownership_and_uniqueness(app, make_user, client, auth):
    a = make_user("a")
    b = make_user("b")

    r = client.post("/saved-commands", json={"name": "c1", "command": "echo"}, headers=auth(a))
    assert r.status_code == 201
    body = r.get_json()["command"]
    assert body["created_by"] == "user"  # criado na UI = pré-aprovado
    cid = body["id"]

    # nome duplicado p/ o mesmo dono → 409
    r = client.post("/saved-commands", json={"name": "c1", "command": "echo"}, headers=auth(a))
    assert r.status_code == 409

    # b não enxerga os comandos de a
    r = client.get("/saved-commands", headers=auth(b))
    assert r.get_json()["commands"] == []

    # b não pode apagar o comando de a
    r = client.delete(f"/saved-commands/{cid}", headers=auth(b))
    assert r.status_code == 404

    # a apaga o próprio
    r = client.delete(f"/saved-commands/{cid}", headers=auth(a))
    assert r.status_code == 200


def test_routine_requires_steps(app, make_user, client, auth):
    a = make_user("a")
    r = client.post("/routines", json={"name": "vazia", "steps": []}, headers=auth(a))
    assert r.status_code == 400
