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


def test_task_sem_campo_teste_nao_inventa_comando_invalido():
    erros, _ = verificar_plano.verificar(_dag("", teste=""), "X")
    assert not any(erro.startswith("E5:") for erro in erros)


@pytest.mark.parametrize("comando", [
    "./.venv/bin/python -m pytest .autodev/tests/ -q",
    ".venv/bin/python3 -m pytest .autodev/tests/ -q",
    ".venv/bin/pytest -q",
    "venv/bin/python -m pytest .autodev/tests/ -q",
    "pytest -q",
])
def test_campo_teste_rejeita_formas_fora_do_runner(comando):
    erros, _ = verificar_plano.verificar(_dag(comando), "X")
    assert any(erro.startswith("E5:") for erro in erros)


@pytest.mark.parametrize("criterio", [
    "edita autodev/alvo.py; executar .venv/bin/python -m pytest -q",
    "edita autodev/alvo.py; a suite roda com .venv/bin/python -m pytest -q",
])
def test_criterio_rejeita_venv_relativo_independente_da_redacao(criterio):
    erros, _ = verificar_plano.verificar(_dag(
        "python3 -m pytest .autodev/tests/ -q",
        criterio=criterio,
    ), "X")
    assert any(erro.startswith("E5:") for erro in erros)


def test_proibicao_em_frase_anterior_nao_mascara_comando_infrator():
    erros, _ = verificar_plano.verificar(_dag(
        "python3 -m pytest .autodev/tests/ -q",
        criterio=("edita autodev/alvo.py. nao use .venv/bin/python. "
                  "rode .venv/bin/python -m pytest -q"),
    ), "X")
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

    texto = plan_prompt.PROMPT_PLANO
    assert "python3 -m pytest" in texto
    linhas_venv = [linha.lower() for linha in texto.splitlines()
                   if ".venv/bin/python" in linha]
    assert linhas_venv
    assert all(any(negacao in linha for negacao in ("não use", "nao use", "proibido", "nunca"))
               for linha in linhas_venv)
    assert "worktree nao tem venv" in texto
    assert plan_prompt.montar_prompt_plano("pedido").endswith("pedido")


def test_verificador_e_somente_leitura(tmp_path):
    sprint = tmp_path / "SPRINT-X"
    sprint.mkdir()
    dag_path = sprint / "dag.json"
    dag_path.write_text(
        '{"tasks":[{"id":"P1","criterios":["edita autodev/alvo.py"],'
        '"teste":"python3 -m pytest .autodev/tests/test_alvo.py -q"}]}',
        encoding="utf-8",
    )
    (sprint / "decisions.md").write_text("inalterado\n", encoding="utf-8")
    antes = {p.relative_to(sprint): p.read_bytes() for p in sprint.rglob("*") if p.is_file()}

    dag, nome = verificar_plano.carregar(str(dag_path))
    verificar_plano.verificar(dag, nome)

    depois = {p.relative_to(sprint): p.read_bytes() for p in sprint.rglob("*") if p.is_file()}
    assert depois == antes


def test_preserva_regras_e2_e3_e4_a1_e_a2():
    comando = "python3 -m pytest .autodev/tests/test_compartilhado.py -q"
    dag = {"tasks": [
        {"id": "P1", "criterios": ["cria autodev/compartilhado.py"],
         "teste": comando, "deps": []},
        {"id": "P2", "criterios": ["edita autodev/compartilhado.py"],
         "teste": comando, "deps": []},
        {"id": "BASE", "criterios": ["cria autodev/base.py"],
         "teste": "python3 -m pytest .autodev/tests/test_base.py -q", "deps": []},
        {"id": "P3", "criterios": ["edita autodev/compartilhado.py"],
         "teste": "python3 -m pytest .autodev/tests/test_p3.py -q", "deps": ["BASE"]},
        {"id": "P4", "criterios": ["resultado sem caminho concreto"],
         "teste": "", "deps": []},
    ]}

    erros, avisos = verificar_plano.verificar(dag, "X")

    assert any(erro.startswith("E2:") for erro in erros)
    assert any(erro.startswith("E3:") for erro in erros)
    assert any(erro.startswith("E4: P4") for erro in erros)
    assert any(aviso.startswith("A1:") for aviso in avisos)
    assert any(aviso.startswith("A2:") for aviso in avisos)


def test_verificador_usa_mesma_regra_de_citacao_do_planner():
    dag = {"tasks": [
        {"id": "P1", "criterios": ["consulta autodev/compartilhado.py"],
         "teste": "python3 -m pytest tests/test_p1.py -q", "deps": []},
        {"id": "P2", "criterios": ["valida autodev/compartilhado.py"],
         "teste": "python3 -m pytest tests/test_p2.py -q", "deps": []},
    ]}

    erros, _ = verificar_plano.verificar(dag, "X")

    assert any(
        erro.startswith("E2:")
        and "P1" in erro
        and "P2" in erro
        and "autodev/compartilhado.py" in erro
        for erro in erros
    )


@pytest.mark.parametrize("criterio", [
    "os testes usam pytest e a suite roda em menos de 30 s",
    "o arquivo de teste cobre o caso de pytest sem comando",
    "rode .venv/bin/python autodev/cli.py plan --sprint X",
])
def test_mencoes_e_cli_nao_sao_comandos_de_teste(criterio):
    erros, _ = verificar_plano.verificar(_dag(
        "python3 -m pytest -q", criterio="edita autodev/alvo.py; " + criterio,
    ), "X")
    assert not any(e.startswith("E5:") for e in erros)


@pytest.mark.parametrize("comando", [
    "make test", "npm test", "python3 -m unittest discover -v",
    "bash .autodev/scripts/rodar.sh", "go test ./...",
])
def test_outras_stacks_continuam_aceitas(comando):
    erros, _ = verificar_plano.verificar(_dag(comando), "X")
    assert not any(e.startswith("E5:") for e in erros)


@pytest.mark.parametrize("comando", [
    "pytest -q", "python -m pytest -q",
    "python3 -m pytest -q && pytest tests/",
])
def test_invocacao_incorreta_tem_diagnostico_sem_culpar_venv(comando):
    erros, _ = verificar_plano.verificar(_dag(comando), "X")
    diagnosticos = [e for e in erros if e.startswith("E5:")]
    assert diagnosticos
    assert all("python3 -m pytest" in e and "worktree nao tem venv" not in e
               for e in diagnosticos)
