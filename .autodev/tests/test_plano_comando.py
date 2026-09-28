import importlib.util
from pathlib import Path

import pytest


RAIZ = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "verificar_plano", RAIZ / ".autodev/scripts/verificar_plano.py"
)
verificar_plano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verificar_plano)


def _dag(comando, *, criterio=None, teste=None):
    return {
        "tasks": [{
            "id": "P04",
            "titulo": "comando",
            "criterios": [criterio if criterio is not None else "edita autodev/alvo.py; testar com " + comando],
            "teste": comando if teste is None else teste,
        }]
    }


def test_python3_pytest_e_aceito_pelo_verificador():
    erros, _ = verificar_plano.verificar(_dag("python3 -m pytest .autodev/tests/ -q"), "X")
    assert not any(erro.startswith("E5:") for erro in erros)


def test_caminho_de_venv_relativo_gera_erro_explicativo():
    erros, _ = verificar_plano.verificar(_dag(".venv/bin/python -m pytest .autodev/tests/ -q"), "X")
    assert any(erro.startswith("E5:") and "worktree nao tem venv" in erro for erro in erros)


def test_campo_teste_exige_python3_m_pytest():
    erros, _ = verificar_plano.verificar(_dag("python3 -m pytest .autodev/tests/ -q", teste="pytest -q"), "X")
    assert any(erro.startswith("E5:") and "python3 -m pytest" in erro for erro in erros)


@pytest.mark.parametrize("comando", [
    "./.venv/bin/python -m pytest .autodev/tests/ -q",
    ".venv/bin/python3 -m pytest .autodev/tests/ -q",
    ".venv/bin/pytest -q",
    "venv/bin/python -m pytest .autodev/tests/ -q",
    "pytest -q",
    "",
])
def test_campo_teste_rejeita_formas_fora_do_runner(comando):
    erros, _ = verificar_plano.verificar(_dag(comando), "X")
    assert any(erro.startswith("E5:") for erro in erros)


def test_criterio_detecta_comando_venv_mas_ignora_proibicao():
    erros, _ = verificar_plano.verificar(_dag(
        "python3 -m pytest .autodev/tests/ -q",
        criterio=("edita autodev/alvo.py; nao use .venv/bin/python; "
                  "testar com python3 -m pytest .autodev/tests/ -q"),
        teste="python3 -m pytest .autodev/tests/ -q",
    ), "X")
    assert not any(erro.startswith("E5:") for erro in erros)

    erros, _ = verificar_plano.verificar(_dag(
        "./.venv/bin/python -m pytest .autodev/tests/ -q",
        criterio="edita autodev/alvo.py; comando: ./.venv/bin/python -m pytest .autodev/tests/ -q",
        teste="python3 -m pytest .autodev/tests/ -q",
    ), "X")
    assert any(erro.startswith("E5:") for erro in erros)


def test_prompt_exige_python3_com_pytest():
    from autodev import plan_prompt

    texto = plan_prompt.PROMPT_PLANEJAMENTO
    assert "python3 -m pytest" in texto
    assert ".venv/bin/python" in texto
    assert "worktree nao tem venv" in texto
