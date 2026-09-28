import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]


def _cmd_plan_source():
    arvore = ast.parse((ROOT / "autodev" / "cli.py").read_text(encoding="utf-8"))
    return next((n for n in arvore.body if isinstance(n, ast.FunctionDef)
                 and n.name == "cmd_plan"), None)


def _corpo_da_secao(readme: str, titulo: str) -> str:
    inicio = re.search(rf"^## {re.escape(titulo)}$", readme, re.MULTILINE)
    assert inicio, f"README não contém a seção {titulo!r}"
    fim = re.search(r"^## ", readme[inicio.end() :], re.MULTILINE)
    return readme[inicio.start() : inicio.end() + (fim.start() if fim else len(readme))]


def test_readme_nao_afirma_cota_configurada_sem_cmd_plan():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cmd_plan = _cmd_plan_source()
    assert cmd_plan is None, "atualize este teste para a assinatura real de cmd_plan"
    assert "consome cota do agente configurado" not in readme


def test_estado_atual_mede_suite_sem_contagem_fixa():
    estado_atual = _corpo_da_secao(
        (ROOT / "README.md").read_text(encoding="utf-8"), "Estado atual"
    )
    assert "python3 -m pytest .autodev/tests/ -q" in estado_atual
    assert not re.search(r"\d+\s+testes passando", estado_atual, re.IGNORECASE)
