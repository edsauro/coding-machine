from autodev.planner import (
    Plano,
    TaskPlano,
    avisos_de_colisao_de_arquivo,
    colisoes_de_arquivo,
    validar_plano,
)


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
        {"task_a": "P01", "task_b": "P02", "arquivo": "autodev/modulo.py", "onda": 1}
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

    assert any("P01" in erro and "P02" in erro and "autodev/modulo.py" in erro for erro in erros)


def test_mencao_de_arquivo_de_producao_sem_edicao_nao_colide():
    plano = plano_com(
        task("P01", "usa colisoes importada de autodev/modulo.py sem reimplementar"),
        task("P02", "altera autodev/modulo.py"),
    )

    assert colisoes_de_arquivo(plano) == []
    assert validar_plano(plano) == []


def test_ids_duplicados_nao_geram_colisao_da_task_com_ela_mesma():
    plano = plano_com(
        task("P01", "altera autodev/primeiro.py"),
        task("P01", "altera autodev/segundo.py"),
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
