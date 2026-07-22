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
    # Caminho do .env que o blueprint /settings lê/escreve — o MESMO arquivo
    # que o cli.py usa (fonte única de verdade). Override só existe pros
    # testes (tests/conftest.py aponta pra um tmpdir, nunca pro .env real
    # do repositório).
    ENV_FILE_PATH = _abs(os.environ.get("HELENA_ENV_FILE", BASE_DIR / ".env"))

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

    # Cérebro do agente: 'gemini' (nuvem) ou 'ollama' (modelo local). Default
    # 'gemini' preserva o comportamento de sempre pra quem não mexer no .env.
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini")

    # Gemini (cérebro do agente)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    # temperatura do agente: baixa = mais consistência de tool-calling (§tier1)
    # (compartilhada entre Gemini e Ollama — não é um parâmetro exclusivo do Gemini)
    GEMINI_TEMPERATURE = float(os.environ.get("GEMINI_TEMPERATURE", "0.5"))
    # modelos de mídia (Fase 3) — SEMPRE via Gemini, o Ollama não gera imagem/TTS
    GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
    GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
    GEMINI_TTS_VOICE = os.environ.get("GEMINI_TTS_VOICE", "Kore")

    # Ollama (cérebro local, LLM_PROVIDER=ollama)
    OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
    # se a Helena sobe/derruba o daemon 'ollama serve' junto do próprio ciclo
    # de vida (helena start/stop, ou serviço de sistema) — '0' desliga, útil
    # se o usuário já gerencia o Ollama por conta própria (ex.: serviço
    # próprio instalado pelo instalador oficial do Ollama)
    OLLAMA_MANAGED = os.environ.get("OLLAMA_MANAGED", "1") != "0"
    OLLAMA_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_REQUEST_TIMEOUT_SECONDS", "300"))
    # limite de iterações do loop de tool-calling (evita loop infinito, §15)
    MAX_TOOL_ITERATIONS = 6
    # memória de longo prazo: acima do teto, consolida notas antigas no perfil
    MEMORY_NOTES_THRESHOLD = int(os.environ.get("MEMORY_NOTES_THRESHOLD", "30"))
    MEMORY_NOTES_KEEP = int(os.environ.get("MEMORY_NOTES_KEEP", "15"))
    # loop de agente autônomo (background jobs iterativos): orçamento maior + timeout
    MAX_JOB_ITERATIONS = int(os.environ.get("MAX_JOB_ITERATIONS", "16"))
    JOB_TIMEOUT_SECONDS = int(os.environ.get("JOB_TIMEOUT_SECONDS", "240"))
    # tarefas de desktop (navegar/clicar/digitar): fluxo ver→agir→conferir gasta
    # muito mais passos que pesquisa textual, por isso orçamento bem maior
    MAX_DESKTOP_JOB_ITERATIONS = int(os.environ.get("MAX_DESKTOP_JOB_ITERATIONS", "60"))
    DESKTOP_JOB_TIMEOUT_SECONDS = int(os.environ.get("DESKTOP_JOB_TIMEOUT_SECONDS", "900"))
    # execução de código em sandbox (bubblewrap): sem rede, sem FS do host
    SANDBOX_TIMEOUT_SECONDS = int(os.environ.get("SANDBOX_TIMEOUT_SECONDS", "10"))
    SANDBOX_MAX_OUTPUT = int(os.environ.get("SANDBOX_MAX_OUTPUT", "10000"))
    # tool executar_shell (controle do computador, com aprovação do usuário)
    SHELL_TIMEOUT_SECONDS = int(os.environ.get("SHELL_TIMEOUT_SECONDS", "60"))
    SHELL_MAX_OUTPUT = int(os.environ.get("SHELL_MAX_OUTPUT", "16000"))
    MAX_SHELL_PER_TURN = int(os.environ.get("MAX_SHELL_PER_TURN", "5"))
    # tool executar_ssh (comando remoto via SSH — mesmo orçamento/aprovação do
    # shell local, SHELL_MAX_OUTPUT/MAX_SHELL_PER_TURN acima)
    SSH_CONNECT_TIMEOUT_SECONDS = int(os.environ.get("SSH_CONNECT_TIMEOUT_SECONDS", "10"))
    SSH_TIMEOUT_SECONDS = int(os.environ.get("SSH_TIMEOUT_SECONDS", "60"))

    # Notificação nativa do SO onde o servidor roda (além da notification_queue
    # pro app mobile) — desliga com HELENA_DESKTOP_NOTIFICATIONS=0 (ex.: VPS headless)
    DESKTOP_NOTIFICATIONS_ENABLED = os.environ.get("HELENA_DESKTOP_NOTIFICATIONS", "1") != "0"

    # Bot do Telegram (opcional): a Helena vira um cliente completo por lá — login
    # com email+senha, chat, mídia, jobs, aprovação de shell e lembretes. Usa
    # long-polling (não precisa de URL pública). Vazio = bot desligado. Configure
    # com `helena config set TELEGRAM_BOT_TOKEN <token>` (pegue no @BotFather) ou
    # pela página de configurações. Requer reiniciar o servidor para valer.
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_POLL_TIMEOUT = int(os.environ.get("TELEGRAM_POLL_TIMEOUT", "50"))

    # Upload: tamanho máximo de arquivo (25 MB)
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024

    # CORS: origens do frontend (ng serve em dev; origem do Capacitor na Fase nativa)
    CORS_ORIGINS = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:4200,http://localhost:8100,capacitor://localhost,"
        "https://localhost,http://localhost",
    ).split(",")
