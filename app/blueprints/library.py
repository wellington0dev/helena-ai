"""CRUD de comandos salvos e listas/rotinas (páginas do app).

Criados aqui = `created_by='user'` (autorados pelo usuário na UI) → execução
pré-aprovada. A Helena cria via tools com `created_by='ai'` (passam pelo card).
"""
from flask import Blueprint, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db, write_lock
from app.models import Routine, SavedCommand

library_bp = Blueprint("library", __name__)


def _uid() -> int:
    return int(get_jwt_identity())


def _owned(model, item_id: int, uid: int):
    obj = db.session.get(model, item_id)
    return obj if obj and obj.user_id == uid else None


def _name_taken(model, uid: int, name: str, exclude_id=None) -> bool:
    q = db.session.query(model).filter(model.user_id == uid, model.name == name)
    if exclude_id is not None:
        q = q.filter(model.id != exclude_id)
    return q.first() is not None


# ---------------- comandos salvos ----------------

@library_bp.get("/saved-commands")
@jwt_required()
def list_commands():
    rows = (
        db.session.query(SavedCommand)
        .filter_by(user_id=_uid())
        .order_by(SavedCommand.name.asc())
        .all()
    )
    return jsonify(commands=[c.to_dict() for c in rows]), 200


@library_bp.post("/saved-commands")
@jwt_required()
def create_command():
    uid = _uid()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    command = (data.get("command") or "").strip()
    if not name or not command:
        return jsonify(error="name e command obrigatórios"), 400
    if _name_taken(SavedCommand, uid, name):
        return jsonify(error="já existe um comando com esse nome"), 409
    with write_lock:
        c = SavedCommand(
            user_id=uid, name=name, description=(data.get("description") or "").strip() or None,
            command=command, created_by="user",
        )
        db.session.add(c)
        db.session.commit()
        out = c.to_dict()
    return jsonify(command=out), 201


@library_bp.put("/saved-commands/<int:cid>")
@jwt_required()
def update_command(cid: int):
    uid = _uid()
    c = _owned(SavedCommand, cid, uid)
    if c is None:
        return jsonify(error="não encontrado"), 404
    data = request.get_json(silent=True) or {}
    with write_lock:
        if "name" in data and data["name"].strip():
            if _name_taken(SavedCommand, uid, data["name"].strip(), exclude_id=cid):
                return jsonify(error="nome em uso"), 409
            c.name = data["name"].strip()
        if "description" in data:
            c.description = (data.get("description") or "").strip() or None
        if "command" in data and data["command"].strip():
            c.command = data["command"].strip()
        db.session.commit()
        out = c.to_dict()
    return jsonify(command=out), 200


@library_bp.delete("/saved-commands/<int:cid>")
@jwt_required()
def delete_command(cid: int):
    c = _owned(SavedCommand, cid, _uid())
    if c is None:
        return jsonify(error="não encontrado"), 404
    with write_lock:
        db.session.delete(c)
        db.session.commit()
    return jsonify(ok=True), 200


# ---------------- listas/rotinas ----------------

def _clean_steps(raw) -> list:
    steps = []
    for p in raw or []:
        if not isinstance(p, dict):
            continue
        kind = p.get("kind") if p.get("kind") in ("command", "shell") else "shell"
        value = (p.get("value") or "").strip()
        if value:
            steps.append({"kind": kind, "value": value})
    return steps


@library_bp.get("/routines")
@jwt_required()
def list_routines():
    rows = (
        db.session.query(Routine)
        .filter_by(user_id=_uid())
        .order_by(Routine.name.asc())
        .all()
    )
    return jsonify(routines=[r.to_dict() for r in rows]), 200


@library_bp.post("/routines")
@jwt_required()
def create_routine():
    uid = _uid()
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    steps = _clean_steps(data.get("steps"))
    if not name or not steps:
        return jsonify(error="name e ao menos 1 passo obrigatórios"), 400
    if _name_taken(Routine, uid, name):
        return jsonify(error="já existe uma lista com esse nome"), 409
    with write_lock:
        r = Routine(
            user_id=uid, name=name, description=(data.get("description") or "").strip() or None,
            steps=steps, created_by="user",
        )
        db.session.add(r)
        db.session.commit()
        out = r.to_dict()
    return jsonify(routine=out), 201


@library_bp.put("/routines/<int:rid>")
@jwt_required()
def update_routine(rid: int):
    uid = _uid()
    r = _owned(Routine, rid, uid)
    if r is None:
        return jsonify(error="não encontrado"), 404
    data = request.get_json(silent=True) or {}
    with write_lock:
        if "name" in data and data["name"].strip():
            if _name_taken(Routine, uid, data["name"].strip(), exclude_id=rid):
                return jsonify(error="nome em uso"), 409
            r.name = data["name"].strip()
        if "description" in data:
            r.description = (data.get("description") or "").strip() or None
        if "steps" in data:
            steps = _clean_steps(data.get("steps"))
            if steps:
                r.steps = steps
        db.session.commit()
        out = r.to_dict()
    return jsonify(routine=out), 200


@library_bp.delete("/routines/<int:rid>")
@jwt_required()
def delete_routine(rid: int):
    r = _owned(Routine, rid, _uid())
    if r is None:
        return jsonify(error="não encontrado"), 404
    with write_lock:
        db.session.delete(r)
        db.session.commit()
    return jsonify(ok=True), 200
