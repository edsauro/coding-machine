from autodev.planner import (
    Plano,
    TaskPlano,
    avisos_de_colisao_de_arquivo,
    colisoes_de_arquivo,
    validar_plano,
)
from autodev import cli


def plano_com(*tasks):
    return Plano("t", "o", ".", list(tasks), "")


def task(id_, criterio, deps=None, teste=""):
    return TaskPlano(id_, id_, [criterio], deps or [], teste=teste)


def test_detecta_colisao_de_arquivo_na_mesma_onda():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py"),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "autodev/modulo.py", "onda": 1}
    ]


def test_arquivo_de_producao_em_ondas_diferentes_nao_colide():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py", deps=["P01"]),
    )

    assert colisoes_de_arquivo(plano) == []


def test_arquivo_de_producao_em_ondas_diferentes_vira_aviso():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py", deps=["P01"]),
    )

    assert avisos_de_colisao_de_arquivo(plano) == [
        {
            "task_a": "P01",
            "task_b": "P02",
            "arquivo": "autodev/modulo.py",
            "onda": 1,
            "onda_b": 2,
        }
    ]


def test_mesmo_arquivo_de_teste_colide_em_ondas_diferentes():
    plano = plano_com(
        task("P01", "implementa a funcionalidade", teste="python3 -m pytest tests/test_plano.py"),
        task(
            "P02",
            "implementa a segunda funcionalidade",
            deps=["P01"],
            teste="python3 -m pytest tests/test_plano.py",
        ),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "tests/test_plano.py", "onda": 1}
    ]


def test_validar_plano_inclui_ids_e_arquivo_da_colisao():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py"),
    )

    erros = validar_plano(plano)

    assert erros == [
        "colisão de arquivo entre P01 e P02: autodev/modulo.py (onda 1)"
    ]


def test_negacao_so_afeta_a_mencao_negada():
    plano = plano_com(
        task("P01", "cria autodev/novo.py e nao altera autodev/modulo.py"),
        task("P02", "altera autodev/novo.py"),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "autodev/novo.py", "onda": 1}
    ]


def test_basename_igual_em_diretorios_diferentes_nao_colide():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera modulo.py"),
    )

    assert colisoes_de_arquivo(plano) == []


def test_cmd_plan_exibe_aviso_de_colisao_entre_ondas(monkeypatch, capsys, tmp_path):
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py", deps=["P01"]),
    )
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    monkeypatch.setattr("autodev.planner.planejar", lambda *_: plano)
    monkeypatch.setattr("autodev.planner.validar_e_ordenar", lambda *_: [["P01"], ["P02"]])
    monkeypatch.setattr("autodev.planner.escrever_sprint", lambda *_: tmp_path / "DEVFACTORY-999")

    assert cli.main(["plan", "pedido"]) == 0
    saida = capsys.readouterr().out
    assert "aviso: colisão de arquivo entre P01 (onda 1) e P02 (onda 2): autodev/modulo.py" in saida


def test_mencao_do_mesmo_arquivo_na_mesma_onda_colide():
    plano = plano_com(
        task("P01", "usa colisoes importada de autodev/modulo.py sem reimplementar"),
        task("P02", "altera autodev/modulo.py"),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "autodev/modulo.py", "onda": 1}
    ]


def test_ids_duplicados_nao_geram_colisao_da_task_com_ela_mesma():
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P01", "altera autodev/modulo.py"),
    )

    assert colisoes_de_arquivo(plano) == []


def test_ciclo_nao_levanta_ao_calcular_colisoes():
    plano = plano_com(
        task("P01", "altera autodev/primeiro.py", deps=["P02"]),
        task("P02", "altera autodev/segundo.py", deps=["P01"]),
    )

    assert colisoes_de_arquivo(plano) == []


def test_arquivo_de_teste_citado_no_criterio_colide_mesmo_sem_verbo_de_edicao():
    plano = plano_com(
        task("P01", "confira a cobertura em tests/test_plano.py"),
        task("P02", "consulte tests/test_plano.py", deps=["P01"]),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "tests/test_plano.py", "onda": 1}
    ]


def test_modulo_de_producao_com_prefixo_test_nao_colide_entre_ondas():
    plano = plano_com(
        task("P01", "altera autodev/test_helpers.py"),
        task("P02", "altera autodev/test_helpers.py", deps=["P01"]),
    )

    assert colisoes_de_arquivo(plano) == []
    assert avisos_de_colisao_de_arquivo(plano) == [
        {
            "task_a": "P01",
            "task_b": "P02",
            "arquivo": "autodev/test_helpers.py",
            "onda": 1,
            "onda_b": 2,
        }
    ]


def test_cmd_plan_recusa_colisao_e_nao_escreve_sprint(monkeypatch, capsys, tmp_path):
    plano = plano_com(
        task("P01", "altera autodev/modulo.py"),
        task("P02", "altera autodev/modulo.py"),
    )
    escrita = []
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    monkeypatch.setattr("autodev.planner.planejar", lambda *_: plano)
    monkeypatch.setattr(
        "autodev.planner.escrever_sprint", lambda *_: escrita.append(True)
    )

    assert cli.main(["plan", "pedido"]) == 1
    saida = capsys.readouterr().out
    assert "P01" in saida
    assert "P02" in saida
    assert "autodev/modulo.py" in saida
    assert escrita == []
