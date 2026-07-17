"""Entrypoint: sobe Flask + SocketIO (threading mode)."""
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.agenda.scheduler import start_scheduler  # noqa: E402
from app.agent.ollama_client import ensure_running as ensure_ollama_running  # noqa: E402
from app.jobs.worker import start_worker  # noqa: E402
from app.notifications_dispatcher import start_desktop_notifier  # noqa: E402
from app.extensions import socketio  # noqa: E402

app = create_app()
# scheduler + worker embarcados (fora de create_app para não subir nos testes)
start_scheduler(app)
start_worker(app)
# sobe 'ollama serve' junto (mesmo grupo de processo) se LLM_PROVIDER=ollama
# e OLLAMA_MANAGED — cobre tanto 'helena start' (pidfile) quanto o serviço
# systemd com a mesma implementação (ver app/agent/ollama_client.py)
ensure_ollama_running(app)
start_desktop_notifier(app)

if __name__ == "__main__":
    # Reloader off por padrão: com threading ele forka um processo-filho que
    # atrapalha (recria o DB, dificulta matar). Ative com HELENA_RELOAD=1.
    reload = os.environ.get("HELENA_RELOAD") == "1"
    host = os.environ.get("HELENA_HOST", "0.0.0.0")
    port = int(os.environ.get("HELENA_PORT", "5000"))
    socketio.run(
        app,
        host=host,
        port=port,
        debug=True,
        use_reloader=reload,
        allow_unsafe_werkzeug=True,
    )
