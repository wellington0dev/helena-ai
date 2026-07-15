"""Tools de controle de desktop (tela/mouse/teclado) — cross-platform.

Autorização (mesmos níveis do shell, via CLI `helena users`):
- `capturar_tela` (VER a tela): exige **principal**;
- mouse/teclado (AGIR sem aprovação por ação): exigem **controle absoluto**.

A screenshot volta com `__inject_image__` — o run_agent injeta a imagem num turno
separado para o modelo enxergar (function_response só leva JSON).
"""
from pathlib import Path

from flask import current_app
from google.genai import types

from app.agent import desktop
from app.extensions import db, write_lock
from app.models import Message, User

DESKTOP_VIEW_DECLS = [  # exige principal (ver a tela)
    types.FunctionDeclaration(
        name="capturar_tela",
        description=(
            "Tira um print da tela atual e VÊ o conteúdo. Use antes de clicar ou "
            "digitar, para saber onde estão as coisas e mirar com precisão. Depois "
            "de cada ação (clique/tecla), capture de novo para conferir o resultado."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="enviar_ultima_captura",
        description=(
            "Envia para o usuário, NO CHAT, a última captura de tela que você tirou "
            "(com capturar_tela). Use quando ele perguntar o que você está fazendo/vendo "
            "e você quiser MOSTRAR a tela atual da tarefa. Sempre é o print mais recente."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
]

DESKTOP_INPUT_DECLS = [  # exige controle absoluto (agir sem aprovação)
    types.FunctionDeclaration(
        name="mover_mouse",
        description=(
            "Move o cursor do mouse para a coordenada (x, y) em pixels — sempre "
            "relativa ao ÚLTIMO print que você viu (capturar_tela), não à resolução "
            "real da tela."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "x": types.Schema(type=types.Type.INTEGER),
                "y": types.Schema(type=types.Type.INTEGER),
            },
            required=["x", "y"],
        ),
    ),
    types.FunctionDeclaration(
        name="clicar",
        description=(
            "Clica o mouse. Opcionalmente move para (x, y) antes — coordenada "
            "relativa ao ÚLTIMO print que você viu. Use `duplo` para duplo-clique."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "botao": types.Schema(type=types.Type.STRING, enum=["left", "right", "middle"]),
                "x": types.Schema(type=types.Type.INTEGER),
                "y": types.Schema(type=types.Type.INTEGER),
                "duplo": types.Schema(type=types.Type.BOOLEAN),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="rolar",
        description="Rola a tela (scroll). Positivo = cima, negativo = baixo.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"quantidade": types.Schema(type=types.Type.INTEGER)},
            required=["quantidade"],
        ),
    ),
    types.FunctionDeclaration(
        name="digitar",
        description="Digita um texto no que estiver focado (como se teclasse).",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"texto": types.Schema(type=types.Type.STRING)},
            required=["texto"],
        ),
    ),
    types.FunctionDeclaration(
        name="tecla",
        description="Pressiona uma tecla ou atalho. Ex.: 'enter', 'esc', 'ctrl+c', 'alt+tab'.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={"atalho": types.Schema(type=types.Type.STRING)},
            required=["atalho"],
        ),
    ),
]


def _deny(user_id: int, need_full: bool, check_avail: bool = True) -> str | None:
    user = db.session.get(User, user_id)
    if user is None:
        return "usuário inválido"
    if need_full and not user.shell_full_control:
        return (
            "Controlar mouse/teclado exige CONTROLE ABSOLUTO. Explique ao usuário "
            "que ele precisa ativar esse nível (helena users fullcontrol) e não tente agir."
        )
    if not need_full and not (user.is_principal or user.shell_full_control):
        return (
            "Só o usuário principal pode ver/controlar a tela. Explique gentilmente "
            "e não tente."
        )
    if check_avail:
        ok, why = desktop.available()
        if not ok:
            return f"controle de desktop indisponível aqui: {why}"
    return None


def _last_shot_path(user_id: int) -> Path:
    """Arquivo temporário com a ÚLTIMA captura (sobrescrito a cada nova)."""
    d = Path(current_app.config["MEDIA_DIR"]) / str(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d / ".last_screenshot.png"


def _new_image_message(user_id: int, png: bytes) -> int:
    """Salva o PNG como mídia e cria uma mensagem de imagem no chat."""
    from app.media import storage  # import tardio (evita ciclo)

    media_url = storage.save_bytes(user_id, png, "png")
    with write_lock:
        msg = Message(
            user_id=user_id, role="assistant", content="",
            media_url=media_url, media_type="image",
            media_meta={"mime": "image/png", "description": "captura de tela"},
        )
        db.session.add(msg)
        db.session.commit()
        return msg.id


def _capturar_tela(user_id: int, args: dict) -> dict:
    err = _deny(user_id, need_full=False)
    if err:
        return {"ok": False, "error": err}
    try:
        png, w, h = desktop.screenshot()
    except desktop.DesktopError as exc:
        return {"ok": False, "error": str(exc)}
    current_app.logger.info("DESKTOP screenshot %dx%d (user=%s)", w, h, user_id)
    # guarda a ÚLTIMA captura (temporária, sobrescrita) p/ poder mostrar depois
    try:
        _last_shot_path(user_id).write_bytes(png)
    except OSError:
        pass
    return {
        "ok": True,
        "info": f"tela capturada ({w}x{h})",
        "largura": w,
        "altura": h,
        "__inject_image__": png,
        "__inject_mime__": "image/png",
    }


def _enviar_ultima_captura(user_id: int, args: dict) -> dict:
    err = _deny(user_id, need_full=False, check_avail=False)
    if err:
        return {"ok": False, "error": err}
    path = _last_shot_path(user_id)
    if not path.exists():
        return {
            "ok": False,
            "error": "ainda não capturei nenhuma tela; use capturar_tela antes de mostrar.",
        }
    msg_id = _new_image_message(user_id, path.read_bytes())
    current_app.logger.info("DESKTOP enviar_ultima_captura (user=%s)", user_id)
    return {"ok": True, "message_id": msg_id, "created": "imagem (última captura da tela)"}


def _act(user_id: int, label: str, fn) -> dict:
    err = _deny(user_id, need_full=True)
    if err:
        return {"ok": False, "error": err}
    try:
        fn()
    except (desktop.DesktopError, KeyError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}
    current_app.logger.info("DESKTOP %s (user=%s)", label, user_id)
    from app import audit
    audit.record(user_id, "desktop", label)  # trilha de auditoria
    return {"ok": True}


def _mover_mouse(user_id: int, args: dict) -> dict:
    x, y = desktop.report_to_real(int(args["x"]), int(args["y"]))
    return _act(user_id, f"mousemove {x},{y}", lambda: desktop.move_mouse(x, y))


def _clicar(user_id: int, args: dict) -> dict:
    x = args.get("x")
    y = args.get("y")
    if x is not None and y is not None:
        x, y = desktop.report_to_real(int(x), int(y))
    botao = args.get("botao", "left")
    duplo = bool(args.get("duplo"))
    return _act(
        user_id,
        f"click {botao}{' x2' if duplo else ''} at {x},{y}",
        lambda: desktop.click(
            button=botao,
            x=int(x) if x is not None else None,
            y=int(y) if y is not None else None,
            double=duplo,
        ),
    )


def _rolar(user_id: int, args: dict) -> dict:
    q = int(args["quantidade"])
    return _act(user_id, f"scroll {q}", lambda: desktop.scroll(q))


def _digitar(user_id: int, args: dict) -> dict:
    texto = args.get("texto", "")
    return _act(user_id, f"type ({len(texto)} chars)", lambda: desktop.type_text(texto))


def _tecla(user_id: int, args: dict) -> dict:
    atalho = args.get("atalho", "")
    return _act(user_id, f"key {atalho!r}", lambda: desktop.press_key(atalho))


DESKTOP_HANDLERS = {
    "capturar_tela": _capturar_tela,
    "enviar_ultima_captura": _enviar_ultima_captura,
    "mover_mouse": _mover_mouse,
    "clicar": _clicar,
    "rolar": _rolar,
    "digitar": _digitar,
    "tecla": _tecla,
}
