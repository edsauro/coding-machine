import ast
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
AFIRMACAO_COTA_CONFIGURADA = re.compile(
    r"consom\w*\s+(?:a\s+)?cota\s+do\s+agente\s+configurado", re.IGNORECASE
)
CONTAGEM_FIXA_DE_TESTES = re.compile(
    r"\b\d+\s*(?:testes?|tests?|passed|verdes|n[oó]s)\b", re.IGNORECASE
)


def _cmd_plan_source():
    arvore = ast.parse((ROOT / "autodev" / "cli.py").read_text(encoding="utf-8"))
    return next((n for n in arvore.body if isinstance(n, ast.FunctionDef)
                 and n.name == "cmd_plan"), None)


def _cmd_plan_repassa_agente_recebido(cmd_plan: ast.FunctionDef | None) -> bool:
    """Distingue parâmetro efetivo de um parâmetro decorativo na assinatura."""
    if cmd_plan is None:
        return False

    parametros = {
        argumento.arg
        for argumento in (
            *cmd_plan.args.posonlyargs,
            *cmd_plan.args.args,
            *cmd_plan.args.kwonlyargs,
        )
    }

    def vem_de_parametro(expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in parametros and expr.id in {"agent", "agente"}
        return (
            isinstance(expr, ast.Attribute)
            and isinstance(expr.value, ast.Name)
            and expr.value.id in parametros
            and expr.attr in {"agent", "agente"}
        )

    for chamada in ast.walk(cmd_plan):
        if not isinstance(chamada, ast.Call):
            continue
        nome = chamada.func.attr if isinstance(chamada.func, ast.Attribute) else ""
        if nome != "planejar":
            continue
        argumentos = list(chamada.args[2:3])
        argumentos.extend(
            palavra.value for palavra in chamada.keywords
            if palavra.arg in {"agent", "agente"}
        )
        if any(vem_de_parametro(argumento) for argumento in argumentos):
            return True
    return False


def _corpo_da_secao(readme: str, titulo: str) -> str:
    inicio = re.search(rf"^## {re.escape(titulo)}$", readme, re.MULTILINE)
    assert inicio, f"README não contém a seção {titulo!r}"
    fim = re.search(r"^## ", readme[inicio.end() :], re.MULTILINE)
    return readme[inicio.start() : inicio.end() + (fim.start() if fim else len(readme))]


def test_readme_nao_afirma_cota_configurada_sem_repassar_agente_recebido():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cmd_plan = _cmd_plan_source()

    if AFIRMACAO_COTA_CONFIGURADA.search(readme):
        assert _cmd_plan_repassa_agente_recebido(cmd_plan), (
            "README atribui a cota ao agente configurado, mas cmd_plan não repassa "
            "um agente recebido para planner.planejar"
        )


def test_estado_atual_mede_suite_sem_contagem_fixa():
    estado_atual = _corpo_da_secao(
        (ROOT / "README.md").read_text(encoding="utf-8"), "Estado atual"
    )
    assert "python3 -m pytest .autodev/tests/ -q" in estado_atual
    assert not CONTAGEM_FIXA_DE_TESTES.search(estado_atual)
