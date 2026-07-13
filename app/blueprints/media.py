"""Upload e serve de mídia, escopado e protegido contra path traversal."""
from flask import Blueprint, jsonify, request, send_file
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.media import storage

media_bp = Blueprint("media", __name__, url_prefix="/media")


def _uid() -> int:
    return int(get_jwt_identity())


@media_bp.post("/upload")
@jwt_required()
def upload():
    """Recebe um arquivo (multipart 'file'), salva na pasta do usuário e devolve
    o media_url relativo + classificação. Ingest (transcrição/descrição) acontece
    quando o arquivo é anexado a uma mensagem em POST /messages."""
    uid = _uid()
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify(error="arquivo 'file' ausente"), 400

    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else ""
    if not ext:
        return jsonify(error="arquivo sem extensão"), 400

    data = file.read()
    if not data:
        return jsonify(error="arquivo vazio"), 400

    media_type, mime = storage.classify(ext)
    media_url = storage.save_bytes(uid, data, ext)
    return jsonify(
        media_url=media_url,
        media_type=media_type,
        media_meta={"mime": mime, "original_name": file.filename, "size": len(data)},
    ), 201


@media_bp.get("/<int:owner_id>/<path:filename>")
@jwt_required()
def serve(owner_id: int, filename: str):
    """Serve um arquivo de mídia. Só o dono autenticado acessa (cross-user 403);
    `resolve` usa safe_join e ancora no diretório do usuário (anti-traversal)."""
    uid = _uid()
    if owner_id != uid:
        return jsonify(error="acesso negado"), 403
    path = storage.resolve(uid, filename)
    if path is None:
        return jsonify(error="não encontrado"), 404
    return send_file(path)
