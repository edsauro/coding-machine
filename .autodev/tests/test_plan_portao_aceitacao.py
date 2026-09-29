"""Aceitação integrada do portão que protege a execução de um plano."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autodev import cli
from autodev.agents import AgenteInfo
from autodev.orchestrator import Orquestrador

from conftest import escreve_fake_spec


SPRINT = "PORTAO"


def _escrever_sprint(raiz: Path, tasks: list[dict]) -> Path:
    sprint = raiz / ".autodev" / "sprints" / SPRINT
    sprint.mkdir(parents=True, exist_ok=True)
    (sprint / "dag.json").write_text(
        json.dumps({"sprint_id": SPRINT, "tasks": tasks}), encoding="utf-8"
    )
    (sprint / "sprint.yaml").write_text(
        f"sprint_id: {SPRINT}\nobjetivo: provar o portao\nstatus: PLANEJADO\n",
        encoding="utf-8",
    )
    return sprint


def _task(task_id: str, criterio: str) -> dict:
    return {
        "id": task_id,
        "titulo": f"entrega {task_id}",
        "criterios": [criterio],
        "deps": [],
        "agente": "codex",
        "teste": "python3 -m pytest -q",
    }


def _orquestrador(raiz: Path) -> Orquestrador:
    orquestrador = Orquestrador(raiz, SPRINT, modo_teste=True)
    orquestrador.disponiveis["agy"] = AgenteInfo(
        nome="agy", disponivel=False, erro="revisor externo desligado no teste"
    )
    return orquestrador


def test_portao_do_plano(tmp_path, monkeypatch, projeto_pytest):
    """Colisão, aprovação e hash impedem chamadas indevidas ao agente real."""
    # A regressão que este teste captura é criar tasks ou invocar o agente antes
    # de validar o DAG/portão. O driver abaixo é o falso determinístico.
    fake_spec = escreve_fake_spec(
        tmp_path / "driver-falso.json",
        {
            "sequencia": [
                {
                    "acao": "editar",
                    "arquivo": "src/calc.py",
                    "conteudo": (
                        "# entregue pelo driver falso\n\n"
                        "def soma(a, b):\n"
                        "    return a + b\n"
                    ),
                }
            ]
        },
    )
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(fake_spec))

    # O validador do motor recusa colisão na mesma onda antes de materializar
    # task, portanto antes de qualquer invocação do driver.
    _escrever_sprint(
        projeto_pytest,
        [_task("P01", "altera src/calc.py"), _task("P02", "altera src/calc.py")],
    )
    with pytest.raises(ValueError, match="colisão de arquivo"):
        _orquestrador(projeto_pytest).rodar()
    assert not fake_spec.with_suffix(".count").exists()

    # Um DAG válido permanece parado enquanto o sprint PLANEJADO não for
    # aprovado; após a aprovação o mesmo driver falso executa e integra a task.
    _escrever_sprint(projeto_pytest, [_task("P01", "altera src/calc.py")])
    parado = _orquestrador(projeto_pytest).rodar()
    assert "sem aprovacao" in (parado.parado_por or "")
    assert not fake_spec.with_suffix(".count").exists()

    monkeypatch.setattr(cli, "RAIZ", projeto_pytest)
    assert cli.main(["aprovar", SPRINT, "--por", "Aceitacao"]) == 0
    executado = _orquestrador(projeto_pytest).rodar()
    assert executado.parado_por is None
    integrada = _orquestrador(projeto_pytest).store.task(SPRINT, "P01")
    assert integrada["estado"] == "INTEGRATED"
    assert fake_spec.with_suffix(".count").read_text(encoding="utf-8") == "1"

    # Qualquer edição posterior do DAG torna o hash aprovado obsoleto e não
    # reabre a execução nem consome outra ação falsa.
    dag = projeto_pytest / ".autodev" / "sprints" / SPRINT / "dag.json"
    dag.write_text(
        json.dumps(
            {"sprint_id": SPRINT, "tasks": [_task("P01", "altera src/calc.py")]},
            indent=2,
        ),
        encoding="utf-8",
    )
    invalido = _orquestrador(projeto_pytest).rodar()
    assert "divergencia" in (invalido.parado_por or "")
    assert fake_spec.with_suffix(".count").read_text(encoding="utf-8") == "1"
