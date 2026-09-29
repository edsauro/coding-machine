import json

from autodev import cli


def _dag(root):
    sprint = root / ".autodev/sprints/S-001"
    sprint.mkdir(parents=True)
    (sprint / "dag.json").write_text(json.dumps({
        "sprint_id": "S-001",
        "tasks": [
            {"id": "P01", "titulo": "um", "criterios": ["edita src/a.py"], "teste": "tests/test_a.py"},
            {"id": "P02", "titulo": "dois", "deps": ["P01"], "criterios": ["edita src/a.py"], "teste": "tests/test_a.py"},
        ],
    }), encoding="utf-8")


def test_prever_imprime_impacto_por_onda(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    _dag(tmp_path)

    assert cli.main(["prever", "S-001"]) == 0
    saida = capsys.readouterr().out
    assert "onda 1" in saida and "P01" in saida
    assert "src/a.py" in saida and "tests/test_a.py" in saida
    assert "colis" in saida.lower()


def test_prever_json_e_valido(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    _dag(tmp_path)

    assert cli.main(["prever", "S-001", "--json"]) == 0
    relatorio = json.loads(capsys.readouterr().out)
    assert relatorio["ondas"] == [["P01"], ["P02"]]
    assert relatorio["arquivos_por_task"]["P01"] == ["src/a.py", "tests/test_a.py"]
    assert relatorio["colisoes"][0]["arquivo"] == "tests/test_a.py"


def test_prever_sprint_inexistente_falha_sem_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)

    assert cli.main(["prever", "NAO-EXISTE"]) == 1
    saida = capsys.readouterr().out
    assert "sprint" in saida.lower() and "não encontrado" in saida.lower()
    assert "Traceback" not in saida
