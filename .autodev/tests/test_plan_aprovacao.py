from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
import pytest

from autodev.cli import main
from autodev.config import carrega_sprint
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


def test_aprovar_preserva_conteudo_existente_do_yaml(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    (d / "sprint.yaml").write_text(
        "# comentario importante\nsprint_id: S1\nobjetivo: teste\n# manter\nstatus: PLANEJADO\n",
        encoding="utf-8")
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["aprovar", "S1", "--por", "Ana"]) == 0
    texto = (d / "sprint.yaml").read_text(encoding="utf-8")
    assert "# comentario importante" in texto
    assert "# manter" in texto


def test_aprovar_substitui_aprovacao_null(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    with (d / "sprint.yaml").open("a", encoding="utf-8") as arquivo:
        arquivo.write("aprovacao: null\n")
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)

    assert main(["aprovar", "S1", "--por", "Ana"]) == 0

    texto = (d / "sprint.yaml").read_text(encoding="utf-8")
    assert texto.count("aprovacao:") == 1
    dados = yaml.safe_load(texto)
    assert dados["aprovacao"]["por"] == "Ana"


@pytest.mark.parametrize("sprint_id", ["", ".", "..", "../fora", r"..\fora"])
def test_aprovar_recusa_sprint_id_invalido(tmp_path, monkeypatch, capsys, sprint_id):
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)

    assert main(["aprovar", sprint_id, "--por", "Ana"]) == 1
    assert "sprint inválido" in capsys.readouterr().out


@pytest.mark.parametrize("aprovacao", [
    {"por": "Ana", "hash_dag": "abc"},
    {"por": "Ana", "quando": "agora", "hash_dag": "abc", "extra": "nao"},
    {"por": "", "quando": "agora", "hash_dag": "abc"},
])
def test_carrega_sprint_recusa_aprovacao_malformada(tmp_path, aprovacao):
    d = _sprint(tmp_path)
    dados = yaml.safe_load((d / "sprint.yaml").read_text())
    dados["aprovacao"] = aprovacao
    (d / "sprint.yaml").write_text(yaml.safe_dump(dados), encoding="utf-8")
    with pytest.raises(ValueError, match="aprovacao"):
        carrega_sprint(d / "sprint.yaml")


def test_aprovar_recusa_nome_vazio(tmp_path, monkeypatch, capsys):
    _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", ""]) == 2
    assert "--por" in capsys.readouterr().out


def test_aprovar_recusa_sprints_divergentes(tmp_path, monkeypatch, capsys):
    _sprint(tmp_path, "S1")
    _sprint(tmp_path, "S2")
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S2", "--por", "Ana"]) == 2
    assert "diverge" in capsys.readouterr().out


def test_run_informa_yaml_de_aprovacao_invalido_sem_traceback(tmp_path, monkeypatch, capsys):
    d = _sprint(tmp_path)
    dados = yaml.safe_load((d / "sprint.yaml").read_text())
    dados["aprovacao"] = {"por": "Ana", "hash_dag": "abc"}
    (d / "sprint.yaml").write_text(yaml.safe_dump(dados), encoding="utf-8")
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "run", "--modo-teste"]) == 2
    saida = capsys.readouterr().out
    assert "sprint invalido" in saida


def test_run_nao_mascara_value_error_do_laco(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["aprovar", "S1", "--por", "Ana"]) == 0

    def falha_no_laco(orquestrador, args):
        raise ValueError("falha interna")

    monkeypatch.setattr("autodev.cli._rodar_com_rodadas", falha_no_laco)
    with pytest.raises(ValueError, match="falha interna"):
        main(["--sprint", "S1", "run", "--modo-teste"])


def test_run_planejado_recusa_sem_aprovacao(tmp_path):
    d = _sprint(tmp_path)
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    resultado = o.rodar()
    assert resultado.parado_por and "autodev aprovar S1 --por <nome>" in resultado.parado_por
    assert o.store.tasks("S1") == []


def test_run_apos_aprovacao_registra_evento(tmp_path, monkeypatch):
    d = _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", "Ana"]) == 0
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    resultado = o.rodar()
    assert resultado.parado_por is None
    assert any(e["tipo"] == "aprovacao_plano" for e in o.store.eventos("S1"))


def test_run_nao_exige_aprovacao_de_sprint_ja_em_execucao(tmp_path):
    _sprint(tmp_path)
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    o.store.transicionar_sprint("S1", "EM_EXECUCAO", "sprint preexistente")

    resultado = o.rodar()

    assert resultado.parado_por is None


def test_aprovacao_gera_um_evento_por_hash(tmp_path, monkeypatch):
    _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["--sprint", "S1", "aprovar", "S1", "--por", "Ana"]) == 0
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    o.rodar()
    o.rodar()
    eventos = [e for e in o.store.eventos("S1") if e["tipo"] == "aprovacao_plano"]
    assert len(eventos) == 1


def test_aprovacao_nao_duplica_evento_fora_da_janela_de_500(tmp_path, monkeypatch):
    _sprint(tmp_path)
    monkeypatch.setattr("autodev.cli.RAIZ", tmp_path)
    assert main(["aprovar", "S1", "--por", "Ana"]) == 0
    o = Orquestrador(tmp_path, "S1", modo_teste=True)
    assert o.rodar().parado_por is None
    for numero in range(501):
        o.store.evento("S1", None, "ruido", {"numero": numero})

    o._registrar_aprovacao()

    quantidade = o.store.conn.execute(
        "SELECT COUNT(*) FROM events WHERE sprint_id=? AND tipo='aprovacao_plano'",
        ("S1",),
    ).fetchone()[0]
    assert quantidade == 1


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
