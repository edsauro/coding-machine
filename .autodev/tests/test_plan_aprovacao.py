from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from autodev.cli import main
from autodev.orchestrator import Orquestrador


def _sprint(tmp_path: Path, sprint: str = "S1") -> Path:
    d = tmp_path / ".autodev" / "sprints" / sprint
    d.mkdir(parents=True)
    (d / "dag.json").write_text(json.dumps({"sprint_id": sprint, "tasks": []}), encoding="utf-8")
    (d / "sprint.yaml").write_text(
        f"sprint_id: {sprint}\nobjetivo: teste\nstatus: PLANEJADO\n", encoding="utf-8"
    )
    return d


def test_sprint_yaml_accepts_approval_keys(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", "Ana"]) == 0
    approval = yaml.safe_load((d / "sprint.yaml").read_text())["aprovacao"]
    assert set(approval) == {"por", "quando", "hash_dag"}
    assert approval["por"] == "Ana"
    assert approval["hash_dag"] == hashlib.sha256((d / "dag.json").read_bytes()).hexdigest()


def test_run_planejado_recusa_sem_aprovacao(tmp_path):
    d = _sprint(tmp_path)
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    resultado = o.rodar()
    assert resultado.parado_por and "autodev aprovar S1 --por <nome>" in resultado.parado_por


def test_run_apos_aprovacao_registra_evento(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", "Ana"]) == 0
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    o.rodar()
    assert any(e["tipo"] == "aprovacao_plano" for e in o.store.eventos("S1"))


def test_dag_alterado_invalida_aprovacao(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", "Ana"]) == 0
    (d / "dag.json").write_text(
        '{"sprint_id":"S1","tasks":[{"id":"P1","titulo":"x",'
        '"criterios":["x"]}]}'
    )
    resultado = Orquestrador(tmp_path, "S1", modo_teste=True).rodar()
    assert resultado.parado_por and "diverg" in resultado.parado_por
