"""Aceitacao do fluxo completo: pedido livre -> sprint executavel."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autodev import cli
from autodev.agents import AgenteInfo
from autodev.config import ordem_topologica
from autodev.orchestrator import Orquestrador
from autodev.planner import (
    PlanoInvalido,
    escrever_sprint,
    parsear_plano,
    planejar,
    validar_e_ordenar,
    validar_plano,
)

from conftest import criar_fixture
from test_acceptance import STATS_CORRIGIDO


def _resposta_do_planejador() -> str:
    return json.dumps(
        {
            "titulo": "Corrigir e documentar estatisticas",
            "objetivo": "Entregar o modulo de estatisticas verificado",
            "repositorio": ".",
            "tasks": [
                {
                    "id": "P01",
                    "titulo": "Corrigir estatisticas",
                    "criterios": [
                        "python3 -m pytest tests/test_stats.py -q passa"
                    ],
                    "deps": [],
                    "agente": "codex",
                    "teste": "python3 -m pytest tests/test_stats.py -q",
                },
                {
                    "id": "P02",
                    "titulo": "Registrar a entrega",
                    "criterios": ["NOTAS.md descreve a entrega verificada"],
                    "deps": [],
                    "agente": "codex",
                    "teste": "python3 -m pytest tests/test_stats.py -q",
                },
                {
                    "id": "P03",
                    "titulo": "Consolidar o resultado",
                    "criterios": ["RESULTADO.md registra o resultado final"],
                    "deps": ["P01", "P02"],
                    "agente": "codex",
                    "teste": "python3 -m pytest tests/test_stats.py -q",
                },
            ],
        },
        ensure_ascii=False,
    )


def _escrever_spec_fake(caminho: Path, conteudo: dict) -> None:
    caminho.write_text(json.dumps(conteudo, ensure_ascii=False), encoding="utf-8")
    caminho.with_suffix(".count").unlink(missing_ok=True)


def test_aceitacao_do_planejador(tmp_path, monkeypatch, capsys):
    raiz = criar_fixture(tmp_path / "projeto")
    (raiz / "src/stats.py").write_text(STATS_CORRIGIDO, encoding="utf-8")
    subprocess.run(["git", "add", "src/stats.py"], cwd=raiz, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture verde"], cwd=raiz, check=True
    )
    logs = raiz / ".autodev/sprints/DEVFACTORY-001/logs"
    logs.mkdir(parents=True)
    fake_spec = tmp_path / "driver-falso.json"
    _escrever_spec_fake(
        fake_spec, {"acao": "echo", "texto": _resposta_do_planejador()}
    )
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(fake_spec))

    plano = planejar(raiz, "Corrija e documente o modulo stats", "codex")

    assert len(plano.tasks) == 3
    assert validar_plano(plano) == []
    destino = escrever_sprint(raiz, plano)
    sprint_id = destino.name

    monkeypatch.setattr(cli, "RAIZ", raiz)
    assert cli.main(["--sprint", sprint_id, "init"]) == 0
    assert "DAG valido: 3 tasks em 2 ondas" in capsys.readouterr().out

    dag = json.loads((destino / "dag.json").read_text(encoding="utf-8"))
    ondas = ordem_topologica(dag)
    assert ondas == [["P01", "P02"], ["P03"]]

    plano_vago = parsear_plano(_resposta_do_planejador())
    plano_vago.tasks[1].criterios = ["Melhorar a documentacao"]
    with pytest.raises(PlanoInvalido, match=r"task P02: critério 1 vago"):
        validar_e_ordenar(plano_vago)

    _escrever_spec_fake(
        fake_spec,
        {
            "sequencia": [
                {
                    "acao": "editar",
                    "arquivo": "ETAPA.md",
                    "conteudo": "# Estatisticas verificadas\n",
                },
                {
                    "acao": "editar",
                    "arquivo": "NOTAS.md",
                    "conteudo": "# Entrega verificada\n",
                },
                {
                    "acao": "editar",
                    "arquivo": "RESULTADO.md",
                    "conteudo": "# Resultado final\n",
                },
            ]
        },
    )
    orquestrador = Orquestrador(raiz, sprint_id, modo_teste=True)
    orquestrador.disponiveis["agy"] = AgenteInfo(
        nome="agy", disponivel=False, erro="desativado no teste"
    )

    resultado = orquestrador.rodar()

    assert resultado.parado_por is None
    assert resultado.concluidas == 3
    assert [
        orquestrador.store.task(sprint_id, task_id)["estado"]
        for task_id in ("P01", "P02", "P03")
    ] == ["INTEGRATED", "INTEGRATED", "INTEGRATED"]
    assert fake_spec.with_suffix(".count").read_text(encoding="utf-8") == "3"
