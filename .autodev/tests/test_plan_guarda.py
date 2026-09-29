import json

import pytest

from autodev import cli
from autodev.config import valida_dag
from autodev.planner import Plano, TaskPlano, avisos_de_colisao_de_arquivo


def dag_com(*tasks):
    return {"tasks": list(tasks)}


def task(id_, criterio, deps=None):
    return {"id": id_, "titulo": id_, "criterios": [criterio], "deps": deps or []}


def test_valida_dag_recusa_arquivo_citado_na_mesma_onda():
    erros = valida_dag(dag_com(
        task("P01", "altera autodev/config.py"),
        task("P02", "altera autodev/config.py"),
    ))

    assert any("P01" in erro and "P02" in erro and "autodev/config.py" in erro
               for erro in erros)


def test_valida_dag_mantem_arquivo_em_ondas_diferentes_valido():
    erros = valida_dag(dag_com(
        task("P01", "altera autodev/config.py"),
        task("P02", "altera autodev/config.py", deps=["P01"]),
    ))

    assert erros == []


def test_valida_dag_detecta_caminhos_equivalentes_na_mesma_onda():
    erros = valida_dag(dag_com(
        task("P01", "altera ./autodev/config.py"),
        task("P02", "altera autodev\\config.py"),
    ))

    assert any("P01" in erro and "P02" in erro and "autodev/config.py" in erro
               for erro in erros)


@pytest.mark.parametrize("referencia", ["item 3.a", "versao 1.b", "e.g. isto"])
def test_valida_dag_nao_confunde_referencia_textual_com_arquivo(referencia):
    erros = valida_dag(dag_com(
        task("P01", f"cobre {referencia}"),
        task("P02", f"documenta {referencia}"),
    ))

    assert erros == []


@pytest.mark.parametrize("arquivo", [
    ".autodev/sprints/S-1/decisions.md",
    ".autodev/sprints/S-1/logs/orquestrador.log",
    ".autodev/sprints/S-1/evidence/resultado.txt",
])
def test_valida_dag_permite_registros_compartilhados_na_mesma_onda(arquivo):
    erros = valida_dag(dag_com(
        task("P01", f"acrescenta registro em {arquivo}"),
        task("P02", f"acrescenta registro em {arquivo}"),
    ))

    assert erros == []


def test_valida_dag_numera_primeira_onda_como_um():
    erros = valida_dag(dag_com(
        task("P01", "altera autodev/config.py"),
        task("P02", "altera autodev/config.py"),
    ))

    assert erros == [
        "colisão de arquivo entre P01 e P02: autodev/config.py (onda 1)"
    ]


def test_mesmo_arquivo_em_ondas_diferentes_emite_aviso_do_plano():
    plano = Plano(
        titulo="Teste", objetivo="Teste", repositorio=".", prompt_original="",
        tasks=[
            TaskPlano("P01", "primeira", ["altera autodev/config.py"]),
            TaskPlano("P02", "segunda", ["altera autodev/config.py"], deps=["P01"]),
        ],
    )

    assert avisos_de_colisao_de_arquivo(plano) == [{
        "task_a": "P01", "task_b": "P02", "arquivo": "autodev/config.py",
        "onda": 1, "onda_b": 2,
    }]


def test_init_recusa_dag_com_colisao_na_mesma_onda(tmp_path, monkeypatch, capsys):
    sprint = "S-COLISAO"
    diretorio = tmp_path / ".autodev" / "sprints" / sprint
    diretorio.mkdir(parents=True)
    (diretorio / "dag.json").write_text(json.dumps(dag_com(
        task("P01", "altera autodev/config.py"),
        task("P02", "altera autodev/config.py"),
    )))
    monkeypatch.setattr(cli, "RAIZ", tmp_path)

    assert cli.main(["--sprint", sprint, "init"]) == 1
    saida = capsys.readouterr().out
    assert "P01" in saida and "P02" in saida and "autodev/config.py" in saida
