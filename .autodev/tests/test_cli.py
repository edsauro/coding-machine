"""Testes do CLI — só o comportamento de erro das operações de ciclo de vida.

Estes comandos mexem no sprint de verdade, então precisam RECUSAR bem: uma
exceção não tratada vira traceback na cara de quem está fechando o sprint, e o
código de saída 0 esconde a falha de qualquer script que chame o CLI.
"""
from __future__ import annotations

from autodev import cli
from autodev.state import StateStore


def _prepara(tmp_path, monkeypatch, sprint="S1"):
    """Aponta o CLI para um banco isolado e devolve o store."""
    monkeypatch.setattr(cli, "RAIZ", tmp_path)
    return StateStore(tmp_path / ".autodev" / "state.db")


def test_encerrar_sprint_ja_encerrado_falha_limpo(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch) as st:
        st.encerrar_sprint("S1", resultado="CONCLUIDO")
    rc = cli.main(["--sprint", "S1", "encerrar"])
    saida = capsys.readouterr().out
    assert rc == 1, "deveria sair com 1, nao 0"
    assert "NAO ENCERRADO" in saida
    assert "ja esta" in saida or "já está" in saida
    assert "Traceback" not in saida


def test_encerrar_recusa_com_task_pendente(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch) as st:
        st.criar_task("S1", "T01", "primeira")
    rc = cli.main(["--sprint", "S1", "encerrar"])
    saida = capsys.readouterr().out
    assert rc == 1
    assert "NAO ENCERRAVEL" in saida
    assert "T01(NEW)" in saida
    assert "Traceback" not in saida


def test_encerrar_forcado_passa_e_avisa(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch) as st:
        st.criar_task("S1", "T01", "primeira")
    rc = cli.main(["--sprint", "S1", "encerrar", "--forcar"])
    saida = capsys.readouterr().out
    assert rc == 0
    assert "FORCADO" in saida
    assert "ficaram em aberto" in saida


def test_evidenciar_task_inexistente_falha_limpo(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch):
        pass
    rc = cli.main(["--sprint", "S1", "evidenciar", "T99", "-e", "x"])
    saida = capsys.readouterr().out
    assert rc == 1
    assert "task inexistente" in saida
    assert "Traceback" not in saida
    # str(KeyError) traz aspas; a mensagem nao deve trazer
    assert "'task inexistente" not in saida


def test_evidenciar_task_ja_concluida_falha_limpo(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch) as st:
        st.criar_task("S1", "T01", "primeira")
        st.concluir_task_evidenciada("S1", "T01", evidencia="ja feito")
    rc = cli.main(["--sprint", "S1", "evidenciar", "T01", "-e", "de novo"])
    saida = capsys.readouterr().out
    assert rc == 1
    assert "ja esta DONE" in saida or "já está DONE" in saida
    assert "Traceback" not in saida


def test_evidenciar_caminho_feliz(tmp_path, monkeypatch, capsys):
    with _prepara(tmp_path, monkeypatch) as st:
        st.criar_task("S1", "T01", "primeira")
    rc = cli.main(["--sprint", "S1", "evidenciar", "T01", "-e", "modulo + testes"])
    saida = capsys.readouterr().out
    assert rc == 0
    assert "DONE" in saida and "retroativo" in saida
