import pytest

from autodev import planner


def task(task_id="P1", deps=None, criterios=None):
    return planner.TaskPlano(
        id=task_id,
        titulo="Implementar validação",
        criterios=["aceita JSON"] if criterios is None else criterios,
        deps=[] if deps is None else deps,
        teste="python3 -m pytest",
    )


def plano(tasks):
    return planner.Plano("Validação", "Validar plano", ".", tasks, "validar")


def test_devolve_ondas_com_dependencias_e_ordem_deterministica():
    entrada = plano([
        task("P4", ["P2", "P3"]), task("P3", ["P1"]),
        task("P2", ["P1"]), task("P1"), task("P0"),
    ])

    assert planner.validar_e_ordenar(entrada) == [["P0", "P1"], ["P2", "P3"], ["P4"]]


@pytest.mark.parametrize("tasks, ids", [
    ([task("P1", ["P2"]), task("P2", ["P3"]), task("P3", ["P1"])],
     ["P1", "P2", "P3"]),
    ([task("P1", ["P1"])], ["P1"]),
])
def test_recusa_ciclo_citando_ids(tasks, ids):
    with pytest.raises(planner.PlanoInvalido, match="ciclo") as erro:
        planner.validar_e_ordenar(plano(tasks))
    assert all(task_id in str(erro.value) for task_id in ids)


@pytest.mark.parametrize("tasks", [
    [], [task(criterios=[])], [task(), task()], [task(deps=["P9"])],
])
def test_recusa_estrutura_invalida(tasks):
    with pytest.raises(planner.PlanoInvalido):
        planner.validar_e_ordenar(plano(tasks))


@pytest.mark.parametrize("verbo", ["melhorar", "otimizar", "refatorar", "revisar", "ajustar"])
def test_recusa_criterio_vago_com_id_e_indice_mesmo_com_teste_na_task(verbo):
    entrada = plano([task("P7", criterios=["aceita JSON", f"{verbo.upper()} o código"])])
    with pytest.raises(planner.PlanoInvalido, match=r"P7.*critério 2"):
        planner.validar_e_ordenar(entrada)


@pytest.mark.parametrize("criterio", [
    "Melhorar autodev/planner.py para recusar ciclos",
    "Revisar README.md para documentar as ondas",
    "Ajustar Makefile para executar a suíte",
    "Otimizar e verificar com python3 -m pytest",
    "Refatorar e executar pytest -q",
    "Revisar e executar npm test",
    "Ajustar e executar go test ./...",
    "Retorna três ondas para o DAG de exemplo",
])
def test_aceita_criterio_concreto(criterio):
    assert planner.validar_e_ordenar(plano([task(criterios=[criterio])])) == [["P1"]]


@pytest.mark.parametrize("criterio", [
    "Melhorar o código. Depois validar.",
    "Revisar os arquivos e testes",
    "Ajustar a relação entrada/saída",
])
def test_prosa_sem_arquivo_ou_comando_nao_torna_criterio_concreto(criterio):
    with pytest.raises(planner.PlanoInvalido, match="P1.*critério 1"):
        planner.validar_e_ordenar(plano([task(criterios=[criterio])]))
