"""Model de usuário (multi-usuário, auth JWT)."""
from datetime import datetime, timezone

import bcrypt

from app.extensions import db
from app.models.types import UtcDateTime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # bookkeeping interno (satisfaz o UNIQUE NOT NULL legado da coluna);
    # gerado automaticamente no registro a partir do email, nunca pedido
    # nem exibido na API/UI.
    username = db.Column(db.Text, unique=True, nullable=False, index=True)
    # nome da conta (pedido no cadastro). Contas antigas ficam None até
    # editarem — DIFERENTE de UserProfile.profile["nome_preferido"] (como a
    # Helena se dirige ao usuário; evolui à parte, não é a identidade da conta).
    name = db.Column(db.Text, nullable=True)
    email = db.Column(db.Text, unique=True, nullable=True)
    password_hash = db.Column(db.Text, nullable=False)
    push_registered = db.Column(db.Boolean, default=False, nullable=False)
    notif_prefs = db.Column(db.JSON, default=dict, nullable=False)
    # usuário principal: só ele pode pedir para a Helena executar comandos no PC
    is_principal = db.Column(db.Boolean, default=False, nullable=False)
    # controle absoluto: executa QUALQUER comando sem pedir aprovação (implica principal)
    shell_full_control = db.Column(db.Boolean, default=False, nullable=False)
    # navegador preferido pra tool abrir_navegador (id do detect_browsers); None = usa o 1º instalado
    default_browser = db.Column(db.Text, nullable=True)
    # diretório de trabalho atual da Helena (onde executar_shell roda e onde ela
    # edita/cria código). O CLI (`helena chat`) envia o cwd do terminal; a Helena
    # também pode navegar com mudar_diretorio. None = home do usuário.
    working_dir = db.Column(db.Text, nullable=True)
    # kill-switch de federação: pausa pareamento/envio/recebimento com peers (junto do panic)
    federation_paused = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(UtcDateTime, default=_utcnow, nullable=False)

    def set_password(self, raw: str) -> None:
        self.password_hash = bcrypt.hashpw(
            raw.encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    def check_password(self, raw: str) -> bool:
        return bcrypt.checkpw(
            raw.encode("utf-8"), self.password_hash.encode("utf-8")
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "push_registered": self.push_registered,
            "notif_prefs": self.notif_prefs,
            "default_browser": self.default_browser,
            "created_at": self.created_at.isoformat(),
        }
