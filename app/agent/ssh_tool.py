"""Tool `executar_ssh`: a Helena roda comandos em OUTRAS máquinas da rede via
SSH — mesmo modelo de confiança do `executar_shell` (aprovação no chat pra
`principal`, direto pra `fullcontrol`, mesmo orçamento por turno), usando as
chaves/agente SSH JÁ configurados na conta que roda o servidor. Nunca pede
senha nem trava esperando uma (ver `shell_tool.run_remote`, `BatchMode=yes`).

Handler fino de propósito: toda a lógica de confiança/aprovação/auditoria já
existe em `shell_tool.py` (reusada aqui via `target_host`), pra não duplicar
o fluxo de segurança em dois lugares que podem divergir.
"""
from google.genai import types

from app.agent.shell_tool import _decide_and_dispatch

SSH_EXECUTAR_DECL = types.FunctionDeclaration(
    name="executar_ssh",
    description=(
        "Executa UM comando em OUTRA máquina da rede via SSH, usando as chaves "
        "já configuradas nesta máquina (nunca pede/usa senha). Use "
        "listar_dispositivos_rede antes se não souber o endereço do destino. "
        "Mesmas regras do executar_shell: o usuário PRECISA autorizar cada "
        "comando novo (a menos que seja controle absoluto); passe UM comando "
        "por chamada; você NÃO deve assumir que rodou até receber a saída. "
        "Comandos com 'sudo' só funcionam se o usuário tiver habilitado sudo "
        "na Helena (helena users sudo); se não tiver, vai ser bloqueado — não "
        "tente contornar reescrevendo o comando."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "host": types.Schema(
                type=types.Type.STRING,
                description="Destino SSH: 'usuario@ip', 'ip', ou um apelido do ~/.ssh/config.",
            ),
            "comando": types.Schema(
                type=types.Type.STRING,
                description="O comando exato a executar na máquina remota.",
            ),
            "motivo": types.Schema(
                type=types.Type.STRING,
                description="Motivo curto (mostrado ao usuário no pedido de permissão).",
            ),
        },
        required=["host", "comando"],
    ),
)


def executar_ssh(user_id: int, args: dict) -> dict:
    host = (args.get("host") or "").strip()
    cmd = (args.get("comando") or args.get("command") or "").strip()
    if not host:
        return {"ok": False, "error": "host vazio"}
    if not cmd:
        return {"ok": False, "error": "comando vazio"}
    motivo = (args.get("motivo") or "").strip()
    return _decide_and_dispatch(user_id, cmd, motivo, target_host=host)
