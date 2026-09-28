"""Comportamento público do subcomando ``autodev plan``."""

from __future__ import annotations

from autodev import cli, planner


def _plano() -> planner.Plano:
    return planner.Plano(
        titulo="Sprint planejada",
        objetivo="Entregar a solicitação",
        repositorio=".",
        tasks=[
            planner.TaskPlano(
                id="P01",
                titulo="Implementar",
                criterios=["python3 -m pytest tests/test_feature.py -q passa"],
                teste="python3 -m pytest tests/test_feature.py -q",
            )
        ],
        prompt_original="",
    )


def test_plan_cria_sprint_e_informa_id_e_caminho(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    recebido = {}

    def planejar(raiz, prompt, agente):
        recebido.update(raiz=raiz, prompt=prompt, agente=agente)
        return _plano()

    monkeypatch.setattr(planner, "planejar", planejar)

    rc = cli.main(["plan", "Criar uma API"])

    destino = tmp_path / ".autodev/sprints/DEVFACTORY-001"
    saida = capsys.readouterr().out
    assert rc == 0
    assert recebido == {
        "raiz": tmp_path,
        "prompt": "Criar uma API",
        "agente": "codex",
    }
    assert (destino / "dag.json").exists()
    assert "DEVFACTORY-001" in saida
    assert str(destino) in saida


def test_plan_le_pedido_de_arquivo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    pedido = tmp_path / "pedido.md"
    pedido.write_text("Planejar a partir deste arquivo\n", encoding="utf-8")
    recebido = {}

    def planejar(raiz, prompt, agente):
        recebido["prompt"] = prompt
        return _plano()

    monkeypatch.setattr(planner, "planejar", planejar)

    assert cli.main(["plan", "--de", str(pedido)]) == 0
    assert recebido["prompt"] == "Planejar a partir deste arquivo\n"
    assert "DEVFACTORY-001" in capsys.readouterr().out


def test_plan_invalido_imprime_cada_erro_e_falha_limpo(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    plano = _plano()
    plano.tasks[0].criterios = []
    plano.tasks.append(
        planner.TaskPlano(id="P02", titulo="Outra", criterios=[], deps=["P99"])
    )
    monkeypatch.setattr(planner, "planejar", lambda *_: plano)

    rc = cli.main(["plan", "Pedido inválido"])

    saida = capsys.readouterr().out
    assert rc == 1
    assert "task P01 sem criterios" in saida
    assert "task P02 sem criterios" in saida
    assert "P02 depende de P99" in saida
    assert "Traceback" not in saida
    assert not (tmp_path / ".autodev/sprints/DEVFACTORY-001").exists()


def test_plan_sprint_ja_existente_falha_limpo(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    monkeypatch.setattr(planner, "planejar", lambda *_: _plano())
    monkeypatch.setattr(
        planner,
        "escrever_sprint",
        lambda *_: (_ for _ in ()).throw(
            planner.SprintJaExiste("sprint já existe: DEVFACTORY-003")
        ),
    )

    rc = cli.main(["plan", "Pedido"])

    saida = capsys.readouterr().out
    assert rc == 1
    assert "sprint já existe: DEVFACTORY-003" in saida
    assert "Traceback" not in saida


def test_plan_help_e_funcional(capsys):
    try:
        cli.main(["plan", "--help"])
    except SystemExit as erro:
        assert erro.code == 0
    else:
        raise AssertionError("argparse deveria encerrar depois de exibir --help")

    saida = capsys.readouterr().out
    assert "--de" in saida
    assert "pedido" in saida
