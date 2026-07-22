"""Painel de desktop (Electron): visão geral só-leitura — quem está usando a
Helena, o que está rodando em segundo plano, e a saúde da máquina. Gate em
`shell_level(user_id) is not None` (principal+, não qualquer logado) — mesmo
critério de `app/agent/network_tools.py`: expõe atividade de OUTROS usuários
e processos do sistema operacional, informação mais sensível que "minhas
próprias configurações" (`/settings`).
"""
from flask import Blueprint, jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func

from app.agent.shell_tool import shell_level
from app.config import DATA_DIR
from app.extensions import db
from app.models import Job, User

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")

_ACTIVE_JOB_STATUSES = ("pending", "running")
_MAX_PROCESSES = 20
_PAYLOAD_TITLE_MAX = 80


def _uid() -> int:
    return int(get_jwt_identity())


def _job_title(payload: dict) -> str:
    title = (payload or {}).get("title") or (payload or {}).get("query") or (payload or {}).get("task") or ""
    title = str(title).strip()
    return title[:_PAYLOAD_TITLE_MAX] if title else "(sem título)"


def _users_overview() -> list[dict]:
    active_counts = dict(
        db.session.query(Job.user_id, func.count(Job.id))
        .filter(Job.status.in_(_ACTIVE_JOB_STATUSES))
        .group_by(Job.user_id)
        .all()
    )
    out = []
    for u in db.session.query(User).order_by(User.id).all():
        out.append({
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "is_principal": u.is_principal,
            "shell_full_control": u.shell_full_control,
            "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
            "active_jobs": active_counts.get(u.id, 0),
        })
    return out


def _jobs_overview() -> list[dict]:
    rows = (
        db.session.query(Job)
        .filter(Job.status.in_(_ACTIVE_JOB_STATUSES))
        .order_by(Job.created_at.desc())
        .all()
    )
    return [
        {
            "id": j.id,
            "user_id": j.user_id,
            "type": j.type,
            "title": _job_title(j.payload),
            "status": j.status,
            "created_at": j.created_at.isoformat(),
        }
        for j in rows
    ]


def _system_overview() -> dict:
    """Best-effort — CPU/RAM/disco. Nunca levanta (painel não pode quebrar
    por causa de uma métrica que falhou nesse ambiente)."""
    import psutil

    system: dict = {}
    try:
        system["cpu_percent"] = psutil.cpu_percent(interval=0.1)
    except Exception:  # noqa: BLE001
        system["cpu_percent"] = None
    try:
        mem = psutil.virtual_memory()
        system["memory"] = {"used": mem.used, "total": mem.total, "percent": mem.percent}
    except Exception:  # noqa: BLE001
        system["memory"] = None
    try:
        disk = psutil.disk_usage(str(DATA_DIR))
        system["disk"] = {"used": disk.used, "total": disk.total, "percent": disk.percent}
    except Exception:  # noqa: BLE001
        system["disk"] = None
    return system


def _processes_overview() -> list[dict]:
    """Top processos por CPU. Cada processo é lido best-effort — numa
    varredura ao vivo é normal um processo sumir/negar acesso no meio."""
    import psutil

    rows = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        rows.append({
            "pid": info.get("pid"),
            "name": info.get("name"),
            "cpu_percent": round(info.get("cpu_percent") or 0.0, 1),
            "memory_percent": round(info.get("memory_percent") or 0.0, 1),
        })
    rows.sort(key=lambda r: r["cpu_percent"], reverse=True)
    return rows[:_MAX_PROCESSES]


@dashboard_bp.get("/overview")
@jwt_required()
def overview():
    if shell_level(_uid()) is None:
        return jsonify(error="sem permissão pra ver o painel — só o usuário principal pode."), 403
    return jsonify(
        users=_users_overview(),
        jobs=_jobs_overview(),
        system=_system_overview(),
        processes=_processes_overview(),
    ), 200
