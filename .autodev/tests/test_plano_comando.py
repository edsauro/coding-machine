import importlib.util
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "verificar_plano", RAIZ / ".autodev/scripts/verificar_plano.py"
)
verificar_plano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verificar_plano)


def _dag(comando):
    return {
        "tasks": [{
            "id": "P04",
            "titulo": "comando",
            "criterios": ["edita autodev/alvo.py; testar com " + comando],
            "teste": comando,
        }]
    }


def test_python3_pytest_e_aceito_pelo_verificador():
    erros, _ = verificar_plano.verificar(_dag("python3 -m pytest .autodev/tests/ -q"), "X")
    assert not any("comando de teste" in erro for erro in erros)


def test_caminho_de_venv_relativo_gera_erro_explicativo():
    erros, _ = verificar_plano.verificar(_dag(".venv/bin/python -m pytest .autodev/tests/ -q"), "X")
    assert any("comando de teste" in erro and "worktree nao tem venv" in erro for erro in erros)


def test_prompt_exige_python3_com_pytest():
    from autodev import plan_prompt

    texto = plan_prompt.PROMPT_PLANEJAMENTO
    assert "python3 -m pytest" in texto
    assert ".venv/bin/python" in texto
    assert "worktree nao tem venv" in texto
