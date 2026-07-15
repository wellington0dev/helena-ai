"""Gating de tools por permissão: normal não recebe as tools de controle da máquina."""
from app.agent.tools import build_tool_declarations


def _names(tool):
    return {d.name for d in tool.function_declarations}


def test_normal_user_has_no_computer_tools(app, make_user):
    uid = make_user("n")
    with app.app_context():
        names = _names(build_tool_declarations(uid))
        for t in ("executar_shell", "capturar_tela", "mover_mouse", "executar_comando"):
            assert t not in names
        # base continua disponível
        for t in ("create_note", "pesquisar", "salvar_comando", "generate_image"):
            assert t in names


def test_principal_has_shell_and_view_but_not_input(app, make_user):
    uid = make_user("p", is_principal=True)
    with app.app_context():
        names = _names(build_tool_declarations(uid))
        assert {"executar_shell", "capturar_tela", "executar_comando"} <= names
        assert "mover_mouse" not in names  # input exige controle absoluto


def test_fullcontrol_has_input_tools(app, make_user):
    uid = make_user("f", is_principal=True, shell_full_control=True)
    with app.app_context():
        names = _names(build_tool_declarations(uid))
        assert {"mover_mouse", "clicar", "digitar", "tecla"} <= names
