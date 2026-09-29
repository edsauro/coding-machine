import json

from autodev import cli
from autodev.config import valida_dag


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


def test_valida_dag_mantem_arquivo_em_ondas_diferentes_como_aviso():
    erros = valida_dag(dag_com(
        task("P01", "altera autodev/config.py"),
        task("P02", "altera autodev/config.py", deps=["P01"]),
    ))

    assert erros == []


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
