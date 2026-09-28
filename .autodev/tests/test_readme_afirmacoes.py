import ast
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _cmd_plan_source():
    arvore = ast.parse((ROOT / "autodev" / "cli.py").read_text(encoding="utf-8"))
    return next((n for n in arvore.body if isinstance(n, ast.FunctionDef)
                 and n.name == "cmd_plan"), None)


def test_readme_nao_afirma_agente_configurado_e_contagem_fixa():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "consome cota do agente configurado" not in readme
    assert "Suíte do orquestrador: 123 testes passando" not in readme
    assert "python3 -m pytest .autodev/tests/ -q" in readme


def test_afirmacao_do_planejador_bate_com_o_codigo_real():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cmd_plan = _cmd_plan_source()
    if "agente `codex`" in readme:
        assert cmd_plan is not None, "README anuncia planejador, mas cmd_plan sumiu"
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "planejar"
            and len(n.args) >= 3
            and isinstance(n.args[2], ast.Constant)
            and n.args[2].value == "codex"
            for n in ast.walk(cmd_plan)
        ), "README anuncia agente fixo codex, mas cmd_plan não o fixa"
