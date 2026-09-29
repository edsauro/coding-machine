from autodev.planner import Plano, TaskPlano, colisoes_de_arquivo, validar_plano


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

    assert any("P01" in erro and "P02" in erro and "autodev/modulo.py" in erro for erro in erros)
