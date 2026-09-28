"""D-18: worktree na base ATUAL e rodadas que nao param no primeiro tropeco.

Dois defeitos reais da rodada de 27/09 22:26->23:02:

1. `WorktreeManager.criar` reusava a branch existente e IGNORAVA a base pedida
   (`git worktree add <caminho> <branch>` nao recria a branch). A P04 nasceu no
   commit anterior a P02/P03, ambas editaram `autodev/planner.py`, e o merge
   conflitou na integracao — a task voltou para retry sem ter culpa nenhuma.
   O DAG era respeitado para ORDEM e violado em CONTEUDO.

2. Uma passada do laco percorre as ondas UMA vez: a falha de integracao deixou as
   dependentes BLOCKED e a rodada terminou (3 de 9). Reabrir a mao era o unico
   caminho. `rearmar_dependentes` devolve para QUEUED as tasks bloqueadas apenas
   por dependencia JA integrada, preservando o contador da escada.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from autodev.worktree import WorktreeManager, git, git_ok  # noqa: E402


def _g(*args: str, cwd: Path) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert p.returncode == 0, f"git {' '.join(args)}: {p.stderr}"
    return p.stdout.strip()


def _escreve(repo: Path, nome: str, texto: str) -> None:
    alvo = repo / nome
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(texto, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path) -> Path:
    r = tmp_path / "projeto"
    r.mkdir()
    _g("init", "-q", "-b", "main", cwd=r)
    _g("config", "user.email", "t@t", cwd=r)
    _g("config", "user.name", "t", cwd=r)
    _escreve(r, "mod.py", "x = 1\n")
    _g("add", "-A", cwd=r)
    _g("commit", "-qm", "base A", cwd=r)
    return r


def _wm(repo: Path) -> WorktreeManager:
    return WorktreeManager(repo, repo / ".autodev" / "worktrees")


# ---------------------------------------------------------------- 1. base atual
def test_branch_de_rodada_anterior_nasce_da_base_atual(repo, tmp_path):
    """Branch que sobrou de rodada anterior volta para base nova, nao fica no commit antigo."""
    wm = _wm(repo)
    a = _g("rev-parse", "HEAD", cwd=repo)
    wt = wm.criar("S", "T01", "codex", base=a)
    assert wt.criado and not wt.realinhado

    # a rodada passa: a integracao anda (commit B) e o worktree e removido,
    # deixando a branch orfa para tras — cenario exato da P04.
    _escreve(repo, "novo.py", "y = 2\n")
    _g("add", "-A", cwd=repo)
    _g("commit", "-qm", "base B", cwd=repo)
    b = _g("rev-parse", "HEAD", cwd=repo)
    _g("worktree", "remove", "--force", str(wt.caminho), cwd=repo)

    wt2 = wm.criar("S", "T01", "codex", base=b)
    assert wt2.realinhado, "a branch velha tinha de ser trazida para a base nova"
    assert _g("rev-parse", "HEAD", cwd=wt2.caminho) == b
    assert (wt2.caminho / "novo.py").exists(), "faltou o codigo integrado depois"
    # o tip antigo fica arquivado para auditoria
    assert _g("rev-parse", "refs/arquivo/sprint/S/T01-codex", cwd=repo) == a


def test_worktree_existente_recebe_a_base_por_merge(repo):
    """Worktree com trabalho proprio continua seu e ganha o codigo novo por merge."""
    wm = _wm(repo)
    a = _g("rev-parse", "HEAD", cwd=repo)
    wt = wm.criar("S", "T01", "codex", base=a)
    _escreve(Path(wt.caminho), "trabalho_da_task.py", "z = 3\n")
    _g("add", "-A", cwd=wt.caminho)
    _g("commit", "-qm", "tarefa trabalhou", cwd=wt.caminho)
    meu = _g("rev-parse", "HEAD", cwd=wt.caminho)

    _escreve(repo, "integrado.py", "w = 4\n")   # outra task integrou depois
    _g("add", "-A", cwd=repo)
    _g("commit", "-qm", "base B", cwd=repo)
    b = _g("rev-parse", "HEAD", cwd=repo)

    wt2 = wm.criar("S", "T01", "codex", base=b)
    assert wt2.realinhado and not wt2.criado
    head = _g("rev-parse", "HEAD", cwd=wt2.caminho)
    assert head != b, "o commit da propria task nao pode ser descartado"
    # os dois lados estao presentes
    assert (wt2.caminho / "integrado.py").exists()
    assert (wt2.caminho / "trabalho_da_task.py").exists()
    assert meu in _g("log", "--format=%H", cwd=wt2.caminho)


def test_worktree_conflitante_volta_para_a_base_e_arquiva(repo):
    """Trabalho escrito contra codigo que nao existe mais: arquiva e volta para a base."""
    wm = _wm(repo)
    a = _g("rev-parse", "HEAD", cwd=repo)
    wt = wm.criar("S", "T01", "codex", base=a)
    _escreve(Path(wt.caminho), "mod.py", "x = 999  # versao antiga da task\n")
    _g("add", "-A", cwd=wt.caminho)
    _g("commit", "-qm", "tarefa mexeu em mod.py", cwd=wt.caminho)
    antigo = _g("rev-parse", "HEAD", cwd=wt.caminho)

    _escreve(repo, "mod.py", "x = 2  # versao integrada\n")
    _g("add", "-A", cwd=repo)
    _g("commit", "-qm", "base B mexeu em mod.py", cwd=repo)
    b = _g("rev-parse", "HEAD", cwd=repo)

    wt2 = wm.criar("S", "T01", "codex", base=b)
    assert wt2.realinhado
    assert _g("rev-parse", "HEAD", cwd=wt2.caminho) == b
    assert "versao integrada" in (wt2.caminho / "mod.py").read_text()
    assert _g("rev-parse", "refs/arquivo/sprint/S/T01-codex", cwd=repo) == antigo


def test_base_ja_contida_nao_mexe_em_nada(repo):
    """Retry normal na mesma onda: nada e realinhado nem arquivado."""
    wm = _wm(repo)
    a = _g("rev-parse", "HEAD", cwd=repo)
    wt = wm.criar("S", "T01", "codex", base=a)
    _escreve(Path(wt.caminho), "t.py", "1\n")
    _g("add", "-A", cwd=wt.caminho)
    _g("commit", "-qm", "trabalho", cwd=wt.caminho)

    wt2 = wm.criar("S", "T01", "codex", base=a)
    assert not wt2.realinhado and not wt2.criado
    assert not git_ok("rev-parse", "--verify", "--quiet",
                      "refs/arquivo/sprint/S/T01-codex", cwd=repo), \
        "nada foi descartado: nao pode haver arquivo"


# ----------------------------------------------------------- 2. rearme automatico
def test_rearme_de_dependentes_preserva_o_contador(tmp_path, monkeypatch):
    """Task BLOCKED so por dependencia integrada volta para QUEUED no mesmo degrau."""
    from conftest import TASK_FIXTURE, criar_fixture, scaffold_sprint
    from autodev.orchestrator import Orquestrador

    fx = criar_fixture(tmp_path / "fixture")
    t2 = dict(TASK_FIXTURE, id="T02", deps=["T01"], titulo="segunda task")
    scaffold_sprint(fx, "SPRINT-D18", [TASK_FIXTURE, t2], objetivo="rearme")
    o = Orquestrador(fx, "SPRINT-D18", modo_teste=True)
    o.carregar()
    o.store.criar_tasks_do_dag("SPRINT-D18", o.dag)

    # T01 integrada; T02 bloqueada so por dependencia, no 3o degrau
    o.store.forcar_estado("SPRINT-D18", "T01", "INTEGRATED", "integrada")
    o.store.bloqueia("SPRINT-D18", "T02", "depende de ['T01']")
    o.store.conn.execute("UPDATE tasks SET tentativas=3, tier_atual=3"
                         " WHERE sprint_id='SPRINT-D18' AND task_id='T02'")
    o.store.conn.commit()

    rearmadas = o.rearmar_dependentes()
    assert rearmadas == ["T02"]
    row = o.store.task("SPRINT-D18", "T02")
    assert row["estado"] == "QUEUED" and row["tentativas"] == 3, \
        "rearmar nao pode reiniciar a escada"
    assert o.store.task("SPRINT-D18", "T02")["bloqueio"] is None
    # idempotente: nao rearma duas vezes
    assert o.rearmar_dependentes() == []
    o.store.close()


def test_rearme_nao_toca_task_bloqueada_por_outro_motivo(tmp_path):
    """BLOCKED por motivo humano (cota, credencial) nao e rearmado sozinho."""
    from conftest import TASK_FIXTURE, criar_fixture, scaffold_sprint
    from autodev.orchestrator import Orquestrador

    fx = criar_fixture(tmp_path / "fixture")
    scaffold_sprint(fx, "SPRINT-D18B", [TASK_FIXTURE], objetivo="rearme 2")
    o = Orquestrador(fx, "SPRINT-D18B", modo_teste=True)
    o.carregar()
    o.store.criar_tasks_do_dag("SPRINT-D18B", o.dag)
    o.store.bloqueia("SPRINT-D18B", "T01", "HAQ-003: precisa de decisao humana")
    assert o.rearmar_dependentes() == []
    assert o.store.task("SPRINT-D18B", "T01")["estado"] == "BLOCKED"
    o.store.close()


def test_run_reabre_sprint_em_fim_antes_de_rodar(monkeypatch):
    """Sprint gravado em FIM (fim de UMA rodada) e reaberto pelo proprio run."""
    from autodev import cli
    import autodev.orchestrator as orch

    class StoreFalso:
        def __init__(self):
            self.reaberturas: list[str] = []

        def estado_sprint(self, sprint_id):
            return "FIM" if not self.reaberturas else "EM_EXECUCAO"

        def reabrir_sprint(self, sprint_id, *, motivo=""):
            self.reaberturas.append(motivo)

    class OrqFalso:
        instancias: list = []

        def __init__(self, *a, **k):
            self.sprint = "S"
            self.store = StoreFalso()
            self.passadas = 0
            OrqFalso.instancias.append(self)

        def rodar(self, parar_em=None):
            self.passadas += 1
            r = orch.ResultadoSprint(sprint="S")
            r.tasks = [orch.ResumoTask("T01", "INTEGRATED", 1)]
            return r

        def rearmar_dependentes(self):
            return []

    monkeypatch.setattr(orch, "Orquestrador", OrqFalso)
    rc = cli.main(["--sprint", "S", "run", "--rodadas", "3", "--modo-teste"])
    assert rc == 0
    o = OrqFalso.instancias[0]
    assert o.passadas == 1, "sem dependente a rearmar, a 1a passada encerra"
    assert o.store.reaberturas == ["passada 1 do run"], \
        "sem reabrir, a passada nasce morta (transicao a partir de FIM e recusada)"


def test_cli_run_da_passadas_e_rearma(monkeypatch, capsys):
    """`run` so para quando nao ha dependente a rearmar (e respeita --rodadas)."""
    from autodev import cli
    import autodev.orchestrator as orch

    class FalsoOrq:
        def __init__(self, *a, **k):
            self.passadas = 0

        def rodar(self, parar_em=None):
            self.passadas += 1
            r = orch.ResultadoSprint(sprint="S")
            r.tasks = [orch.ResumoTask("T01", "INTEGRATED", 1)]
            return r

        def rearmar_dependentes(self):
            return ["T02"] if self.passadas == 1 else []

    monkeypatch.setattr(orch, "Orquestrador", FalsoOrq)
    criados: list[FalsoOrq] = []
    original = FalsoOrq.__init__

    def guarda(self, *a, **k):
        original(self, *a, **k)
        criados.append(self)

    monkeypatch.setattr(FalsoOrq, "__init__", guarda)

    rc = cli.main(["--sprint", "S", "run", "--rodadas", "5", "--modo-teste"])
    assert rc == 0
    assert criados and criados[0].passadas == 2, \
        "a 2a passada roda (ha dependente a rearmar) e a 3a nao existe (nada a rearmar)"
    assert "rearmada" in capsys.readouterr().out
