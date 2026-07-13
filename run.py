"""Entrypoint: sobe Flask + SocketIO (threading mode)."""
import os

from dotenv import load_dotenv

load_dotenv()

from app import create_app  # noqa: E402
from app.agenda.scheduler import start_scheduler  # noqa: E402
from app.jobs.worker import start_worker  # noqa: E402
from app.extensions import socketio  # noqa: E402

app = create_app()
# scheduler + worker embarcados (fora de create_app para não subir nos testes)
start_scheduler(app)
start_worker(app)

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
