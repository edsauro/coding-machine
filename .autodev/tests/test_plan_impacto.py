from autodev.plan_impacto import impacto
from autodev.planner import Plano, TaskPlano


def task(task_id, criterio, deps=None, teste=""):
    return TaskPlano(task_id, task_id, [criterio], deps or [], teste=teste)


def plano(tasks):
    return Plano("Plano", "Objetivo", ".", tasks, "")


def test_retorna_ondas_arquivos_colisoes_e_tasks_sem_arquivo():
    entrada = plano([
        task("P02", "edita autodev/b.py", deps=["P01"]),
        task("P01", "edita autodev/a.py"),
        task("P03", "implementa a lógica", deps=["P01"]),
    ])

    assert impacto(entrada) == {
        "ondas": [["P01"], ["P02", "P03"]],
        "arquivos_por_task": {
            "P01": ["autodev/a.py"],
            "P02": ["autodev/b.py"],
            "P03": [],
        },
        "colisoes": [],
        "sem_arquivo": ["P03"],
    }


def test_retorna_colisao_do_planner_e_nao_altera_o_plano():
    entrada = plano([
        task("P01", "edita autodev/modulo.py"),
        task("P02", "edita autodev/modulo.py"),
    ])

    relatorio = impacto(entrada)

    assert relatorio["colisoes"] == [{
        "task_a": "P01", "task_b": "P02",
        "arquivo": "autodev/modulo.py", "onda": 1,
    }]
    assert entrada.tasks[0].criterios == ["edita autodev/modulo.py"]
