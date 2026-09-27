"""Testes de git, sandbox, runner de testes e integração.

Cobre spec §25: criação/reuso de worktree, posse de agente, test runner,
branch de integração, isolamento.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autodev import sandbox, testrunner
from autodev.integration import Integrador
from autodev.state import WorktreeOcupado
from autodev.worktree import (WorktreeManager, arquivos_alterados, branch_existe,
                              commit_atual, git)

# Os testes que verificam o isolamento olham de FORA, a partir do host. Quando a
# suíte roda dentro do sandbox (AUTODEV_SANDBOX=1, posto pelo próprio sandbox) o
# HOME já é o efêmero /tmp/home, e as premissas deles deixam de valer: não dá
# para aninhar sandbox sem confundir o HOME real com o efêmero. Sem este skip a
# suíte do Coding_Machine falhava 2 testes dentro do sandbox — e como o
# orquestrador roda os testes das tasks DENTRO do sandbox, toda task da sprint
# falhava junto.
SOB_SANDBOX = os.environ.get("AUTODEV_SANDBOX") == "1"
NAO_ANINHAVEL = pytest.mark.skipif(
    SOB_SANDBOX, reason="verifica o isolamento a partir do host; não é aninhável")


# ---------------------------------------------------------------- T04 worktrees
def test_worktree_criado_com_branch_correto(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T01", "codex")
    assert wt.caminho.exists() and (wt.caminho / ".git").exists()
    assert wt.branch == "sprint/S1/T01-codex"
    assert branch_existe(repo, wt.branch)
    assert wt.criado is True


def test_worktree_criacao_e_idempotente(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    a = wm.criar("S1", "T01", "codex")
    b = wm.criar("S1", "T01", "codex")
    assert b.criado is False, "o segundo pedido deve REUSAR, nao recriar"
    assert a.caminho == b.caminho


def test_worktrees_de_tasks_diferentes_sao_distintos(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    a = wm.criar("S1", "T01", "codex")
    b = wm.criar("S1", "T02", "agy")
    assert a.caminho != b.caminho
    assert a.branch != b.branch
    assert len(wm.listar()) >= 3  # principal + 2


def test_commit_e_arquivos_alterados(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    base = commit_atual(repo)
    wt = wm.criar("S1", "T01", "codex", base=base)
    (wt.caminho / "novo.py").write_text("x = 1\n")
    alterados = arquivos_alterados(wt.caminho, base)
    assert "novo.py" in alterados
    sha = wm.commit(wt.caminho, "adiciona novo.py")
    assert sha and sha != base
    assert arquivos_alterados(wt.caminho, base) == ["novo.py"]


def test_commit_sem_mudanca_devolve_none(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T01", "codex")
    assert wm.commit(wt.caminho, "nada") is None


def test_worktree_limpo_antes_de_alterar(repo):
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T01", "codex")
    assert git("status", "--porcelain", cwd=wt.caminho) == ""


# ---------------------------------------------------------------- posse (T15)
def test_um_unico_escritor_por_worktree(store):
    store.criar_task("S1", "T01")
    store.criar_task("S1", "T02")
    store.adquirir_worktree("/wt/x", "T01", "codex")
    with pytest.raises(WorktreeOcupado):
        store.adquirir_worktree("/wt/x", "T02", "agy")
    # o mesmo dono pode readquirir
    store.adquirir_worktree("/wt/x", "T01", "codex")
    store.liberar_worktree("/wt/x")
    store.adquirir_worktree("/wt/x", "T02", "agy")


# ---------------------------------------------------------------- T07 sandbox
def test_sandbox_monta_worktree_escrevivel(tmp_path):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "a.txt").write_text("oi")
    spec = sandbox.SandboxSpec(worktree=str(wt))
    p = sandbox.rodar(spec, ["sh", "-c", "cat a.txt && echo novo > b.txt"])
    assert p.returncode == 0
    assert "oi" in p.stdout
    assert (wt / "b.txt").read_text().strip() == "novo", "escrita deve chegar ao host"


@NAO_ANINHAVEL
def test_sandbox_esconde_caminhos_proibidos(tmp_path, cfg):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    wt = tmp_path / "wt"
    wt.mkdir()
    spec = sandbox.SandboxSpec(worktree=str(wt))
    proibidos = [p for p in cfg.caminhos_proibidos()
                 if Path(p.replace("~", str(Path.home()))).exists()]
    if not proibidos:
        pytest.skip("nenhum caminho proibido existe nesta maquina")
    r = sandbox.verificar_isolamento(spec, proibidos)
    assert r["ok"], f"caminhos vazando para dentro do sandbox: {r['visiveis']}"
    assert len(r["escondidos"]) == len(proibidos)


@NAO_ANINHAVEL
def test_sandbox_home_e_efemero(tmp_path):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    wt = tmp_path / "wt"
    wt.mkdir()
    p = sandbox.rodar(sandbox.SandboxSpec(worktree=str(wt)),
                      ["sh", "-c", "echo $HOME; test -d $HOME && echo EXISTE"])
    assert "/tmp/home" in p.stdout
    assert str(Path.home()) not in p.stdout, "o HOME real nao pode aparecer"


def test_sandbox_sem_rede_quando_pedido(tmp_path):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    wt = tmp_path / "wt"
    wt.mkdir()
    spec = sandbox.SandboxSpec(worktree=str(wt), permite_rede=False)
    p = sandbox.rodar(spec, ["sh", "-c", "getent hosts exemplo.invalid || echo SEM-REDE"])
    assert "SEM-REDE" in p.stdout or p.returncode != 0


# ---------------------------------------------------------------- T08 runner
def test_runner_detecta_pytest(projeto_pytest):
    r = testrunner.rodar(projeto_pytest, usar_sandbox=False)
    assert r.detectado_por in ("pytest", "pytest-tests")
    assert r.passou and r.exit_code == 0
    assert r.passed == 1


def test_runner_detecta_falha_e_gera_assinatura(tmp_path):
    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "test_x.py").write_text("def test_x():\n    assert 1 == 2\n")
    (proj / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    r = testrunner.rodar(proj, usar_sandbox=False)
    assert not r.passou and r.exit_code != 0
    assert r.assinatura, "falha precisa produzir assinatura para o anti-loop"


def test_assinatura_estavel_entre_execucoes(tmp_path):
    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)
    (proj / "tests" / "test_x.py").write_text(
        "def test_x():\n    assert 1 == 2, 'mesma falha'\n")
    (proj / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    a = testrunner.rodar(proj, usar_sandbox=False)
    b = testrunner.rodar(proj, usar_sandbox=False)
    assert a.assinatura == b.assinatura, "a mesma falha deve dar a mesma assinatura"


def test_runner_salva_evidencia_em_disco(tmp_path, projeto_pytest):
    r = testrunner.rodar(projeto_pytest, usar_sandbox=False,
                         evidencia_dir=tmp_path / "ev", rotulo="T01")
    assert r.evidencias and Path(r.evidencias[0]).exists()
    assert "pytest" in Path(r.evidencias[0]).read_text()


def test_tdd_vermelho_antes_da_implementacao(tmp_path):
    proj = tmp_path / "p"
    (proj / "tests").mkdir(parents=True)
    proj_t = proj / "tests" / "test_novo.py"
    proj_t.write_text("def test_novo():\n    assert False, 'vermelho'\n")
    r = testrunner.testes_vermelhos_antes(proj, "tests/test_novo.py")
    assert r["vermelho"] is True, "um teste novo precisa falhar antes"


def test_runner_com_sandbox(tmp_path, projeto_pytest):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    r = testrunner.rodar(projeto_pytest, usar_sandbox=True)
    assert r.execucao_ok and r.passou


# ---------------------------------------------------------------- T12 integração
def test_branch_de_integracao_e_criado(repo, cfg):
    branch = cfg.policies["integracao"]["branch_sprint"]
    integ = Integrador(repo, "sprint/S1/integration")
    wt = integ.garantir_worktree()
    assert wt.exists()
    assert branch_existe(repo, "sprint/S1/integration")
    # idempotente
    assert integ.garantir_worktree() == wt


def test_merge_de_task_e_portoes(repo, cfg):
    # o repositório precisa ter testes de verdade para o portão ser significativo
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_base.py").write_text("def test_base():\n    assert True\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "testes base", cwd=repo)

    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T01", "codex")
    (wt.caminho / "feature.py").write_text("def f():\n    return 42\n")
    (wt.caminho / "tests" / "test_feature.py").write_text(
        "from feature import f\n\n\ndef test_f():\n    assert f() == 42\n")
    wm.commit(wt.caminho, "feature")

    integ = Integrador(repo, "sprint/S1/integration")
    wt_int = integ.garantir_worktree()
    r = integ.merge_task("T01", wt.branch, wt_int)
    assert r.merge_ok, r.conflito
    assert r.commit

    portoes = integ.rodar_portoes(wt_int)
    nomes = [p.nome for p in portoes]
    assert "seguranca" in nomes and "testes" in nomes
    assert all(p.ok for p in portoes), [(p.nome, p.detalhe) for p in portoes if not p.ok]


def test_portao_de_testes_reprova_suite_quebrada(repo, cfg):
    """Um merge que quebra os testes NÃO pode passar pelo portão."""
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / "test_base.py").write_text("def test_base():\n    assert True\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "testes base", cwd=repo)

    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T02", "codex")
    (wt.caminho / "tests" / "test_quebrado.py").write_text(
        "def test_ruim():\n    assert 1 == 2\n")
    wm.commit(wt.caminho, "quebra a suite")

    integ = Integrador(repo, "sprint/S1/integration")
    wt_int = integ.garantir_worktree()
    assert integ.merge_task("T02", wt.branch, wt_int).merge_ok
    portoes = integ.rodar_portoes(wt_int)
    testes = [p for p in portoes if p.nome == "testes"][0]
    assert not testes.ok, "suite quebrada deve reprovar"


def test_portao_de_seguranca_pega_segredo(tmp_path):
    d = tmp_path / "x"
    d.mkdir()
    # A chave é MONTADA em runtime de propósito. Escrita como literal, este
    # arquivo passaria a casar com o próprio padrão que o portão procura — e o
    # portão reprovaria a si mesmo, derrubando a integração inteira do sprint.
    # Não é hipótese: aconteceu, e a integração foi rejeitada por causa do teste
    # do portão. O que o portão lê é o arquivo escrito, então a montagem em
    # runtime mantém o teste fiel.
    falso = "sk-" + "x" * 20
    (d / "config.py").write_text(f'API_KEY = "{falso}"\n')
    integ = Integrador(d, "b")
    p = integ._portao_seguranca(d)
    assert not p.ok, "segredo embutido deve reprovar o portao"


def test_integracao_nunca_toca_main(repo, cfg):
    main_antes = commit_atual(repo)
    wm = WorktreeManager(repo, repo / ".autodev" / "worktrees")
    wt = wm.criar("S1", "T01", "codex")
    (wt.caminho / "f.py").write_text("1\n")
    wm.commit(wt.caminho, "f")
    integ = Integrador(repo, "sprint/S1/integration")
    wt_int = integ.garantir_worktree()
    integ.merge_task("T01", wt.branch, wt_int)
    assert commit_atual(repo) == main_antes, "main NAO pode ser alterada"
    # o worktree de integracao recebeu o merge
    assert (wt_int / "f.py").exists()
