import pytest
from dataclasses import asdict
import importlib.util
from pathlib import Path

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


@pytest.mark.parametrize("arquivo", ["test_plano.py", "modulo_test.py", "conftest.py", "autodev/test_helpers.py"])
def test_nome_de_arquivo_de_teste_colide_entre_ondas(arquivo):
    plano = plano_com(
        task("P01", f"escreve {arquivo}"),
        task("P02", f"altera {arquivo}", deps=["P01"]),
    )
    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": arquivo, "onda": 1}
    ]
    assert avisos_de_colisao_de_arquivo(plano) == []
    assert any(arquivo in erro and "P01" in erro and "P02" in erro
               for erro in validar_plano(plano))


@pytest.mark.parametrize("outro, colide", [("autodev/modulo.py", True), ("modulo.py", False), ("outro/modulo.py", False)])
def test_caminho_windows_preserva_diretorio(outro, colide):
    plano = plano_com(task("P01", r"edita autodev\modulo.py"), task("P02", f"edita {outro}"))
    assert bool(colisoes_de_arquivo(plano)) is colide


def verificar_tasks(*tasks):
    caminho = Path(__file__).resolve().parents[1] / "scripts/verificar_plano.py"
    spec = importlib.util.spec_from_file_location("verificador_colisoes", caminho)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo.verificar({"tasks": [asdict(t) for t in tasks]}, "X")


@pytest.mark.parametrize("criterio", [
    "usa funcao importada de autodev/modulo.py em vez de reimplementar",
    "nao altera autodev/modulo.py",
])
def test_a1_nao_confunde_mencao_com_edicao(criterio):
    _, avisos = verificar_tasks(task("P01", "cria autodev/modulo.py"), task("P02", criterio, deps=["P01"]))
    assert not any(a.startswith("A1:") for a in avisos)


@pytest.mark.parametrize("arquivo", ["dag.json", "spec.md", "sprint.yaml", "decisions.md"])
def test_a1_nao_exige_preservacao_de_metadados(arquivo):
    _, avisos = verificar_tasks(task("P01", f"cria {arquivo}"), task("P02", f"altera {arquivo}", deps=["P01"]))
    assert not any(a.startswith("A1:") for a in avisos)


@pytest.mark.parametrize("preserva", [False, True])
def test_a1_considera_preservacao_da_task_posterior_com_lista_invertida(preserva):
    posterior = "altera autodev/modulo.py" + (" preservando funcoes existentes" if preserva else "")
    _, avisos = verificar_tasks(task("P02", posterior, deps=["P01"]), task("P01", "cria autodev/modulo.py"))
    a1 = [a for a in avisos if a.startswith("A1:")]
    assert bool(a1) is not preserva
    if a1:
        assert "A1: P02" in a1[0]


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


@pytest.mark.parametrize("deps", [[], ["P01"]])
def test_colisao_preserva_nome_de_teste_com_varios_pontos(deps):
    plano = plano_com(
        task("P01", "cria tests/plano.spec.ts"),
        task("P02", "edita tests/plano.spec.ts", deps=deps),
    )

    assert colisoes_de_arquivo(plano) == [
        {"task_a": "P01", "task_b": "P02", "arquivo": "tests/plano.spec.ts", "onda": 1}
    ]
    assert any("tests/plano.spec.ts" in erro for erro in validar_plano(plano))


def test_arquivos_com_extensoes_distintas_nao_colidem():
    plano = plano_com(
        task("P01", "cria tests/plano.spec.ts"),
        task("P02", "cria tests/plano.spec.js"),
    )

    assert colisoes_de_arquivo(plano) == []
    assert validar_plano(plano) == []
