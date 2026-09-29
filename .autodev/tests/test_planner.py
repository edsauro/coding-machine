from autodev.planner import Plano, TaskPlano, validar_plano


def task(task_id="T1", deps=None, criterios=None):
    return TaskPlano(
        id=task_id,
        titulo=f"Tarefa {task_id}",
        criterios=["entrega verificável"] if criterios is None else criterios,
        deps=[] if deps is None else deps,
        agente="codex",
        teste="python3 -m pytest",
    )


def plano(tasks):
    return Plano(
        titulo="Sprint de teste",
        objetivo="Validar o planejador",
        repositorio=".",
        tasks=tasks,
        prompt_original="criar um planejador",
    )


def test_plano_valido_nao_tem_erros():
    assert validar_plano(plano([task("T1"), task("T2", deps=["T1"])])) == []


def test_recusa_plano_sem_tasks():
    assert validar_plano(plano([]))


def test_recusa_task_sem_criterios():
    assert validar_plano(plano([task(criterios=[])]))


def test_recusa_ids_duplicados():
    assert validar_plano(plano([task("T1"), task("T1")]))


def test_recusa_dependencia_inexistente():
    assert validar_plano(plano([task("T1", deps=["T9"])]))


def test_recusa_ciclo_entre_dependencias():
    assert validar_plano(plano([task("T1", deps=["T2"]), task("T2", deps=["T1"])]))
