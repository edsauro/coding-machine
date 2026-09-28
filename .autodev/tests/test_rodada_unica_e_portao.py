"""D-24/D-25 — rodada única por sprint, batimento de vida e escopo do portão de segurança.

Contexto (28/09/2026, DEVFACTORY-004): um vigia de cron disparou uma SEGUNDA rodada
enquanto a primeira revisava a P04 por 5 min. A segunda integrou a P01 no meio da
revisão, o portão de segurança reprovou por um texto pré-existente de outra sprint
(falso positivo sobre a doc da D-22) e a P04 foi bloqueada por limite. O run morreu com
`TransicaoInvalida: P04: BLOCKED -> DONE não permitido`.

Os testes abaixo travam as três correções:
  1. o motor recusa rodada dupla (run_lock), retoma lock órfão e só libera o próprio;
  2. o batimento renova run_lock E writer_lock (revisão longa não parece "rodada morta");
  3. o portão de segurança separa achado novo (bloqueia) de pré-existente (avisa);
  4. BLOCKED -> DONE é transição legal (aprovação que chega depois do bloqueio).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from autodev.config import TRANSICOES
from autodev.integration import Integrador


# --------------------------------------------------------------- rodada única
def test_segunda_rodada_com_pid_vivo_e_recusada(store):
    """Rodada dupla é o defeito que derrubou a sprint 4 — o motor tem de recusar."""
    ok, _ = store.adquirir_run_lock("SPRINT-X", pid=os.getpid())
    assert ok
    outro = os.getpid() + 1          # pid vivo e diferente (o teste roda em processo vivo)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(type(store), "_pid_vivo", staticmethod(lambda p: True))
        ok2, motivo = store.adquirir_run_lock("SPRINT-X", pid=outro)
    assert ok2 is False
    assert "rodada viva" in motivo


def test_rodada_assume_lock_de_processo_morto(store):
    """Crash não pode travar o sprint para sempre: lock órfão é assumido."""
    store.adquirir_run_lock("SPRINT-X", pid=999_999)          # pid inexistente
    store.conn.execute("UPDATE run_lock SET heartbeat=? WHERE sprint_id=?", (0.0, "SPRINT-X"))
    store.conn.commit()
    ok, motivo = store.adquirir_run_lock("SPRINT-X", pid=os.getpid())
    assert ok, motivo
    assert store.run_lock("SPRINT-X")["pid"] == os.getpid()


def test_libera_apenas_o_proprio_lock(store):
    """Rodada antiga que termina depois não pode derrubar o lock do dono novo."""
    store.adquirir_run_lock("SPRINT-X", pid=os.getpid())
    store.liberar_run_lock("SPRINT-X", pid=os.getpid() + 7)   # pid que não é o dono
    assert store.run_lock("SPRINT-X") is not None
    store.liberar_run_lock("SPRINT-X", pid=os.getpid())
    assert store.run_lock("SPRINT-X") is None


def test_batimento_renova_run_lock_e_writer_lock(store):
    """Revisão de LLM leva minutos: sem batimento, quem observa vê 'rodada morta'."""
    store.adquirir_run_lock("SPRINT-X", pid=os.getpid())
    store.criar_task("SPRINT-X", "T01", "tarefa de teste")
    store.conn.execute(
        "INSERT INTO writer_lock (worktree, task_id, agent, pid, heartbeat)"
        " VALUES ('/tmp/wt','T01','codex',0,0)")
    store.conn.commit()
    store.renovar_lock("SPRINT-X", pid=os.getpid())
    rl = store.run_lock("SPRINT-X")["heartbeat"]
    wl = store.conn.execute("SELECT heartbeat FROM writer_lock WHERE task_id='T01'").fetchone()[0]
    assert rl and time.time() - rl < 5
    assert wl and time.time() - wl < 5


def test_blocked_para_done_e_permitido():
    """Aprovação da revisão chegando depois do bloqueio por limite tem de finalizar."""
    assert "DONE" in TRANSICOES["BLOCKED"]
    assert "QUEUED" in TRANSICOES["BLOCKED"]      # rearme continua possível


# ------------------------------------------------- escopo do portão de segurança
def _repo_com_segredo(tmp_path: Path) -> Path:
    """Repo mínimo com um arquivo antigo contendo forma de chave de API."""
    (tmp_path / "antigo").mkdir()
    (tmp_path / "antigo" / "doc.md").write_text(
        'API_KEY = "' + "sk-" + "a" * 20 + '"\n', encoding="utf-8")
    (tmp_path / "novo").mkdir()
    (tmp_path / "novo" / "modulo.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_segredo_pre_existente_nao_bloqueia(tmp_path):
    """O caso da sprint 4: texto antigo de OUTRA sprint congelava toda integração."""
    repo = _repo_com_segredo(tmp_path)
    i = Integrador(repo, "branch", "SPRINT-X")
    p = i._portao_seguranca(repo, arquivos_mudados={"novo/modulo.py"})
    assert p.ok, p.detalhe
    assert "PRE-EXISTENTES" in p.detalhe and "antigo/doc.md" in p.detalhe


def test_segredo_no_arquivo_mudado_bloqueia(tmp_path):
    """Recorte não pode virar porta aberta: segredo NO diff continua reprovando."""
    repo = _repo_com_segredo(tmp_path)
    i = Integrador(repo, "branch", "SPRINT-X")
    p = i._portao_seguranca(repo, arquivos_mudados={"antigo/doc.md"})
    assert not p.ok
    assert "antigo/doc.md" in p.detalhe


def test_sem_recorte_mantem_comportamento_antigo(tmp_path):
    """Sem `arquivos_mudados` (chamada direta/CLI), vale a varredura da árvore."""
    repo = _repo_com_segredo(tmp_path)
    i = Integrador(repo, "branch", "SPRINT-X")
    p = i._portao_seguranca(repo)
    assert not p.ok
