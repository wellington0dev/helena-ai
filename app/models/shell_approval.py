"""Comandos exatos que o usuário confiou ("permitir sempre" = só o comando idêntico).

Um `executar_shell`/`executar_ssh` cujo (comando, destino) exatos estejam
aqui roda sem pedir permissão de novo. Escopado por `target_host` (`""` =
local): confiar num comando numa máquina não confia o mesmo comando em
outra. `target_host` é string vazia (não NULL) DE PROPÓSITO — em SQL, NULL
não é "igual" a NULL pra fins de UNIQUE, então duas aprovações locais
("" 'x2') passariam pela constraint sem barrar duplicata; string vazia
participa da unicidade normalmente."""
from datetime import datetime, timezone

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ShellApproval(db.Model):
    __tablename__ = "shell_approvals"
    __table_args__ = (
        db.UniqueConstraint("user_id", "command", "target_host", name="uq_user_command_host"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    command = db.Column(db.Text, nullable=False)
    target_host = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)
