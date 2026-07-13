"""Configuração da aplicação, carregada de variáveis de ambiente."""
import os
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _abs(path: str | Path) -> Path:
    """Resolve um caminho para absoluto, ancorando relativos em BASE_DIR."""
    p = Path(path)
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


# Sempre absoluto: Flask-SQLAlchemy resolve URIs sqlite relativas contra o
# instance_path do Flask (não o cwd), o que quebra caminhos como ./data.
DATA_DIR = _abs(os.environ.get("HELENA_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _sqlite_uri(env_value: str | None) -> str:
    """Constrói a URI do banco garantindo caminho absoluto para sqlite."""
    if not env_value:
        return f"sqlite:///{DATA_DIR / 'helena.db'}"
    prefix = "sqlite:///"
    if env_value.startswith(prefix) and not env_value.startswith("sqlite:////"):
        # relativa → absolutiza
        rel = env_value[len(prefix):]
        return f"sqlite:///{_abs(rel)}"
    return env_value  # já absoluta ou outro dialeto


class Config:
    # Banco: SQLite local, um arquivo em data/ (sempre absoluto)
    SQLALCHEMY_DATABASE_URI = _sqlite_uri(os.environ.get("DATABASE_URL"))
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # JWT
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "dev-insecure-change-me")
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=30)

    # Mídia no filesystem: data/media/<user_id>/...
    MEDIA_DIR = _abs(os.environ.get("HELENA_MEDIA_DIR", DATA_DIR / "media"))

    # Chat: quantas mensagens cruas mandar ao modelo / gatilho do resumo rolante
    CHAT_WINDOW = 10

    # Gemini (cérebro do agente)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    # modelos de mídia (Fase 3)
    GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    GEMINI_TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")
    # limite de iterações do loop de tool-calling (evita loop infinito, §15)
    MAX_TOOL_ITERATIONS = 6
    # loop de agente autônomo (background jobs iterativos): orçamento maior + timeout
    MAX_JOB_ITERATIONS = int(os.environ.get("MAX_JOB_ITERATIONS", "16"))
    JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", "240"))
    # tool executar_shell (controle do computador, com aprovação do usuário)
    SHELL_TIMEOUT_SECONDS = int(os.environ.get("SHELL_TIMEOUT_SECONDS", "60"))
    SHELL_MAX_OUTPUT = int(os.environ.get("SHELL_MAX_OUTPUT", "16000"))
    MAX_SHELL_PER_TURN = int(os.environ.get("MAX_SHELL_PER_TURN", "5"))

    # Upload: tamanho máximo de arquivo (25 MB)
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024

    # CORS: origens do frontend (ng serve em dev; origem do Capacitor na Fase nativa)
    CORS_ORIGINS = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:4200,http://localhost:8100,capacitor://localhost,"
        "https://localhost,http://localhost",
    ).split(",")
