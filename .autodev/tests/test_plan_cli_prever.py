import json

from autodev import cli


def _dag(root):
    sprint = root / ".autodev/sprints/S-001"
    sprint.mkdir(parents=True)
    (sprint / "dag.json").write_text(json.dumps({
        "sprint_id": "S-001",
        "versao": 1,
        "criterio_paralelizacao": "dependências definem as ondas",
        "tasks": [
            {"id": "P01", "titulo": "um", "criterios": ["edita src/a.py"],
             "deps": [], "agente": "codex", "paralelizavel": True,
             "estimativa": "S", "teste": "tests/test_a.py"},
            {"id": "P02", "titulo": "dois", "deps": ["P01"],
             "criterios": ["edita src/a.py"], "agente": "codex",
             "paralelizavel": True, "estimativa": "S", "teste": "tests/test_a.py"},
        ],
    }), encoding="utf-8")


def test_prever_imprime_impacto_por_onda(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    _dag(tmp_path)

    assert cli.main(["prever", "S-001"]) == 0
    saida = capsys.readouterr().out
    assert "onda 1" in saida and "P01" in saida
    assert "src/a.py" in saida and "tests/test_a.py" in saida
    assert "P01 x P02: tests/test_a.py" in saida


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
    captura = capsys.readouterr()
    saida = captura.out + captura.err
    assert "sprint" in saida.lower() and "não encontrado" in saida.lower()
    assert "Traceback" not in saida


def test_prever_dag_com_raiz_invalida_falha_sem_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    sprint = tmp_path / ".autodev/sprints/S-INVALIDO"
    sprint.mkdir(parents=True)
    (sprint / "dag.json").write_text("[]", encoding="utf-8")

    assert cli.main(["prever", "S-INVALIDO", "--json"]) == 1
    captura = capsys.readouterr()
    resposta = json.loads(captura.out)
    assert "não foi possível prever" in resposta["erro"]
    assert "Traceback" not in captura.out + captura.err


def test_prever_json_malformado_falha_sem_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    sprint = tmp_path / ".autodev/sprints/S-QUEBRADO"
    sprint.mkdir(parents=True)
    (sprint / "dag.json").write_text("{oops", encoding="utf-8")

    assert cli.main(["prever", "S-QUEBRADO"]) == 1
    captura = capsys.readouterr()
    assert "não foi possível prever" in captura.out
    assert "Traceback" not in captura.out + captura.err


def test_prever_dag_invalido_falha_sem_traceback(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    _dag(tmp_path)
    dag_path = tmp_path / ".autodev/sprints/S-001/dag.json"
    dag = json.loads(dag_path.read_text(encoding="utf-8"))
    dag["tasks"][1]["id"] = "P01"
    dag_path.write_text(json.dumps(dag), encoding="utf-8")

    assert cli.main(["prever", "S-001"]) == 1
    captura = capsys.readouterr()
    assert "DAG inválido" in captura.out
    assert "ids duplicados" in captura.out
    assert "Traceback" not in captura.out + captura.err


def test_prever_rejeita_sprint_id_com_traversal(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    fora = tmp_path / ".autodev/FORA"
    fora.mkdir(parents=True)
    (fora / "dag.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")

    assert cli.main(["prever", "../FORA"]) == 1
    captura = capsys.readouterr()
    assert "sprint inválido" in captura.out
    assert "Traceback" not in captura.out + captura.err


def test_prever_mostra_task_sem_arquivo_e_sem_colisao(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    sprint = tmp_path / ".autodev/sprints/S-SEM-ARQUIVO"
    sprint.mkdir(parents=True)
    (sprint / "dag.json").write_text(json.dumps({
        "sprint_id": "S-SEM-ARQUIVO", "versao": 1,
        "criterio_paralelizacao": "dependências definem as ondas",
        "tasks": [{"id": "P01", "titulo": "sem arquivo",
                   "criterios": ["implementa a lógica"], "deps": [],
                   "agente": "codex", "paralelizavel": True,
                   "estimativa": "S", "teste": ""}],
    }), encoding="utf-8")

    assert cli.main(["prever", "S-SEM-ARQUIVO"]) == 0
    saida = capsys.readouterr().out
    assert "(sem arquivo nomeado)" in saida
    assert "colisões:\n  nenhuma" in saida
    assert "tasks sem arquivo nomeado: P01" in saida
