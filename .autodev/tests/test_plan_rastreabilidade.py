"""Rastreabilidade literal e compatibilidade do relatório com planos antigos."""
import json

import pytest

from autodev import cli
from autodev.planner import escrever_sprint, parsear_plano


PROMPT = '  Crie um plano  executável.\n\nPreserve "acentuação" e espaços.  \n'


def _plano(**campos):
    return parsear_plano(json.dumps({
        "titulo": "Rastreabilidade",
        "objetivo": "Registrar o pedido",
        "repositorio": "projeto",
        "tasks": [{"id": "P01", "titulo": "Registrar pedido",
                   "criterios": ["spec.md contém o pedido"],
                   "teste": "python3 -m pytest .autodev/tests/ -q"}],
        **campos,
    }))


def _contexto(repo, destino, monkeypatch):
    monkeypatch.setattr(cli, "RAIZ", repo)
    assert cli.main(["--sprint", destino.name, "report"]) == 0
    texto = (destino / "SPRINT-REPORT.md").read_text(encoding="utf-8")
    return texto.split("## Contexto\n", 1)[1].split("\n---", 1)[0]


def test_preserva_prompt_literal_na_spec_dag_e_report(repo, monkeypatch):
    destino = escrever_sprint(repo, _plano(prompt_original=PROMPT))
    assert "# Prompt original\n\n" + PROMPT in (
        destino / "spec.md").read_text(encoding="utf-8")
    dag = json.loads((destino / "dag.json").read_text(encoding="utf-8"))
    assert dag["prompt_original"] == PROMPT
    contexto = _contexto(repo, destino, monkeypatch)
    assert "Prompt original" in contexto
    assert PROMPT in contexto


def test_plano_sem_prompt_original_ponta_a_ponta(repo, monkeypatch):
    destino = escrever_sprint(repo, _plano())
    assert "nao informado" in _contexto(repo, destino, monkeypatch)


@pytest.mark.parametrize("conteudo", [
    None, b"{", b"[]", b"null", b"42", b'"texto"', b"\xff",
    b"{}", b'{"prompt_original": null}', b'{"prompt_original": 123}',
    b'{"prompt_original": []}',
])
def test_report_tolera_dag_ausente_ou_invalido(repo, monkeypatch, conteudo):
    destino = escrever_sprint(repo, _plano())
    dag = destino / "dag.json"
    dag.unlink()
    if conteudo is not None:
        dag.write_bytes(conteudo)
    assert "nao informado" in _contexto(repo, destino, monkeypatch)


def test_report_tolera_erro_de_leitura_do_dag(repo, monkeypatch):
    destino = escrever_sprint(repo, _plano())
    dag = destino / "dag.json"
    dag.unlink()
    dag.mkdir()  # IsADirectoryError é um OSError real, mesmo sob root.
    assert "nao informado" in _contexto(repo, destino, monkeypatch)


def test_prompt_do_dag_prevalece_sobre_extras(repo, monkeypatch):
    destino = escrever_sprint(repo, _plano(prompt_original=PROMPT))
    (destino / "report-extras.json").write_text(json.dumps({
        "prompt_original": "pedido desatualizado",
    }), encoding="utf-8")
    contexto = _contexto(repo, destino, monkeypatch)
    assert PROMPT in contexto
    assert "pedido desatualizado" not in contexto
