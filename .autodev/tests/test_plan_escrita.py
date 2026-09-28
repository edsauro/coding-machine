import json
from pathlib import Path

import pytest
import yaml

from autodev.planner import Plano, SprintJaExiste, TaskPlano, escrever_sprint


def _plano():
    return Plano(
        titulo="Persistir planejamento",
        objetivo="Produzir um sprint executável",
        repositorio="projeto",
        tasks=[
            TaskPlano(
                id="P01",
                titulo="Criar arquivos",
                criterios=["python3 -m pytest .autodev/tests/ -q passa"],
                deps=[],
                agente="codex",
                teste="python3 -m pytest .autodev/tests/ -q",
            ),
            TaskPlano(
                id="P02",
                titulo="Validar arquivos",
                criterios=["dag.json preserva os campos da task"],
                deps=["P01"],
                agente="agy",
                teste="python3 -m pytest .autodev/tests/test_plan_escrita.py -q",
            ),
        ],
        prompt_original="Crie um planejador\nsem alterar meu pedido.",
    )


def test_escreve_sprint_completo_no_proximo_id(tmp_path):
    (tmp_path / ".autodev/sprints/DEVFACTORY-002").mkdir(parents=True)

    destino = escrever_sprint(tmp_path, _plano())

    assert destino == tmp_path / ".autodev/sprints/DEVFACTORY-003"
    assert {item.name for item in destino.iterdir()} == {
        "spec.md", "dag.json", "sprint.yaml"
    }

    dag = json.loads((destino / "dag.json").read_text(encoding="utf-8"))
    assert set(dag) == {"sprint_id", "versao", "tasks", "prompt_original"}
    assert dag["sprint_id"] == "DEVFACTORY-003"
    assert dag["versao"] == 1
    assert dag["tasks"] == [
        {
            "id": task.id,
            "titulo": task.titulo,
            "criterios": task.criterios,
            "deps": task.deps,
            "agente": task.agente,
            "teste": task.teste,
        }
        for task in _plano().tasks
    ]

    sprint = yaml.safe_load((destino / "sprint.yaml").read_text(encoding="utf-8"))
    assert sprint == {
        "sprint_id": "DEVFACTORY-003",
        "titulo": "Persistir planejamento",
        "objetivo": "Produzir um sprint executável",
        "status": "PLANEJADO",
        "repositorio": {"raiz": "projeto", "merge_em_main": False},
    }

    spec = (destino / "spec.md").read_text(encoding="utf-8")
    assert spec.startswith(
        "# Prompt original\n\nCrie um planejador\nsem alterar meu pedido."
    )


def test_recusa_colisao_sem_sobrescrever(tmp_path, monkeypatch):
    sprints = tmp_path / ".autodev/sprints"
    existente = sprints / "DEVFACTORY-001"
    existente.mkdir(parents=True)
    sentinela = existente / "spec.md"
    sentinela.write_text("conteúdo anterior", encoding="utf-8")
    mkdir_original = Path.mkdir

    def mkdir_com_colisao(path, *args, **kwargs):
        if path.name == "DEVFACTORY-002":
            raise FileExistsError(path)
        return mkdir_original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir_com_colisao)

    with pytest.raises(SprintJaExiste, match="DEVFACTORY-002"):
        escrever_sprint(tmp_path, _plano())

    assert sentinela.read_text(encoding="utf-8") == "conteúdo anterior"
