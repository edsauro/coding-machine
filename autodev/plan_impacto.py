"""Relatório puro do impacto de um plano de execução."""

from __future__ import annotations

from . import config
from .planner import Plano, _arquivos_da_task, colisoes_de_arquivo


def impacto(plano: Plano) -> dict:
    """Resume ondas, arquivos citados e colisões sem alterar o plano.

    O relatório não acessa o filesystem nem invoca agentes; todos os dados são
    derivados apenas da estrutura recebida.
    """
    ondas = config.ordem_topologica({
        "tasks": [{"id": task.id, "deps": task.deps} for task in plano.tasks]
    })
    arquivos_por_task = {
        task.id: sorted(_arquivos_da_task(task)) for task in plano.tasks
    }
    return {
        "ondas": ondas,
        "arquivos_por_task": arquivos_por_task,
        "colisoes": colisoes_de_arquivo(plano),
        "sem_arquivo": [
            task.id for task in plano.tasks if not arquivos_por_task[task.id]
        ],
    }
