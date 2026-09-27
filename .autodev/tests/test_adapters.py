"""Testes dos adaptadores de agente, do revisor e do relatório.

Cobre spec §25: adaptador Codex, adaptador AGY, runner, fluxo de revisão,
detecção de cota, geração de relatório.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autodev import agents, errors, report, review
from autodev.agents import Invocacao, invocar
from autodev.worktree import git

from conftest import escreve_fake_spec

SPRINT = "S1"


# ---------------------------------------------------------------- T01 detecção
def test_deteccao_confere_binario_e_versao():
    d = agents.detectar()
    assert {"codex", "agy", "hermes"} <= set(d)
    for nome, info in d.items():
        assert isinstance(info.disponivel, bool)
        if info.disponivel:
            assert info.binario, f"{nome} disponivel sem caminho de binario"


def test_agente_inexistente_nao_quebra_deteccao(monkeypatch):
    monkeypatch.setattr(agents.shutil, "which", lambda _x: None)
    d = agents.detectar()
    assert all(not i.disponivel for i in d.values())
    assert all(i.erro for i in d.values())


# ---------------------------------------------------------------- T05/T06 adaptadores
@pytest.fixture
def captura(monkeypatch):
    """Captura a linha de comando montada para o agente."""
    caixa = {}

    def fake_run(cmd, **kw):
        caixa["cmd"] = cmd
        caixa["input"] = kw.get("input")
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)
    return caixa


def test_adaptador_codex_passa_modelo_e_effort(captura, tmp_path):
    inv = Invocacao(agente="codex", prompt="faca X", worktree=str(tmp_path),
                    modelo="gpt-5.6-luna", effort="low", edita=True, timeout=60)
    r = invocar(inv, None)
    cmd = captura["cmd"]
    assert "ask-codex" in cmd[0]
    assert "-m" in cmd and "gpt-5.6-luna" in cmd
    assert "-e" in cmd and "low" in cmd
    assert str(tmp_path) in cmd
    assert "workspace-write" in cmd, "editar=True deve liberar escrita"
    # prompt vai por stdin, não pela linha de comando
    assert captura["input"] == "faca X"
    assert r.modelo_usado == "gpt-5.6-luna" and r.effort_usado == "low"


def test_adaptador_codex_modo_leitura(captura, tmp_path):
    inv = Invocacao(agente="codex", prompt="revise", worktree=str(tmp_path),
                    edita=False, timeout=60)
    invocar(inv, None)
    assert "read-only" in captura["cmd"]


def test_adaptador_agy_passa_modelo(captura, tmp_path):
    inv = Invocacao(agente="agy", prompt="revise", worktree=str(tmp_path),
                    modelo="Claude Sonnet 4.5", edita=False, timeout=60)
    r = invocar(inv, None)
    cmd = captura["cmd"]
    assert "ask-agy" in cmd[0]
    assert "Claude Sonnet 4.5" in cmd
    assert "plan" in cmd, "edita=False deve usar o modo plano"
    assert r.modelo_usado == "Claude Sonnet 4.5"


def test_adaptador_agy_modo_edicao(captura, tmp_path):
    inv = Invocacao(agente="agy", prompt="implemente", worktree=str(tmp_path),
                    edita=True, timeout=60)
    invocar(inv, None)
    assert "accept-edits" in captura["cmd"]


def test_adaptador_agy_passa_timeout_com_unidade(captura, tmp_path):
    """Regressão: ask-agy repassa o timeout para `--print-timeout`, que é uma
    DURAÇÃO. Sem unidade, o AGY morre com "missing unit in duration" e o Sprint
    perde o revisor cruzado em silêncio."""
    inv = Invocacao(agente="agy", prompt="revise", worktree=str(tmp_path),
                    edita=False, timeout=900)
    invocar(inv, None)
    cmd = captura["cmd"]
    i = cmd.index("--timeout")
    assert cmd[i + 1] == "900s", f"timeout do AGY deve ter unidade: {cmd[i + 1]}"


def test_adaptador_codex_passa_timeout_em_segundos(captura, tmp_path):
    """O Codex, ao contrário, quer segundos crus."""
    inv = Invocacao(agente="codex", prompt="x", worktree=str(tmp_path),
                    edita=True, timeout=900)
    invocar(inv, None)
    cmd = captura["cmd"]
    i = cmd.index("--timeout")
    assert cmd[i + 1] == "900", f"timeout do Codex deve ser segundos crus: {cmd[i+1]}"


def test_adaptador_agy_passa_d_no_headless(captura, tmp_path):
    """Regressão: o AGY headless AUTO-NEGA qualquer ferramenta sem -D, devolvendo
    stdout vazio com exit 0. O resultado parece sucesso e o revisor some."""
    for edita in (True, False):
        inv = Invocacao(agente="agy", prompt="x", worktree=str(tmp_path),
                        edita=edita, timeout=60)
        invocar(inv, None)
        assert "-D" in captura["cmd"], f"agy edita={edita} sem -D"


def test_adaptador_usa_f_dash_para_stdin(captura, tmp_path):
    """Regressão: o wrapper lê stdin com `-f -`; um `-` solto vira
    'opção desconhecida'."""
    for ag in ("codex", "agy"):
        inv = Invocacao(agente=ag, prompt="x", worktree=str(tmp_path), timeout=60)
        invocar(inv, None)
        cmd = captura["cmd"]
        assert cmd[-2:] == ["-f", "-"], f"{ag}: prompt deve ir por '-f -': {cmd[-3:]}"


def test_sucesso_silencioso_e_tratado_como_falha(tmp_path, monkeypatch):
    """Regressão: exit 0 com stdout vazio e stderr reclamando NÃO é sucesso.

    Foi assim que o bug de permissão do AGY passou despercebido: o revisor
    devolvia nada, com código de saída zero.
    """
    import subprocess
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout="",
            stderr="a tool required the 'command' permission that headless mode "
                   "cannot prompt for")
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)
    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    inv = Invocacao(agente="agy", prompt="revise", worktree=str(tmp_path), timeout=60)
    r = invocar(inv, None)
    assert r.failure_class, "sucesso silencioso precisa virar falha classificada"


def test_sucesso_com_saida_util_continua_sucesso(tmp_path, monkeypatch):
    import subprocess
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout='{"veredito":"APPROVE"}',
                                           stderr="")
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)
    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    inv = Invocacao(agente="codex", prompt="x", worktree=str(tmp_path), timeout=60)
    r = invocar(inv, None)
    assert r.ok and not r.failure_class


def test_agente_sem_adaptador_devolve_erro_de_ambiente(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)
    inv = Invocacao(agente="agente_inexistente", prompt="x", worktree=str(tmp_path))
    r = invocar(inv, None)
    assert r.exit_code == 127
    assert r.failure_class == errors.FailureClass.ENVIRONMENT_ERROR.value


def test_log_do_agente_e_gravado(tmp_path, captura):
    inv = Invocacao(agente="codex", prompt="p", worktree=str(tmp_path),
                    log_path=str(tmp_path / "l.log"))
    invocar(inv, None)
    assert (tmp_path / "l.log").exists()
    assert "ok" in (tmp_path / "l.log").read_text()


# ---------------------------------------------------------------- cota (T12)
def test_cota_detectada_no_driver_fake(tmp_path, fake_agent):
    p = escreve_fake_spec(tmp_path / "f.json", {"acao": "quota"})
    inv = Invocacao(agente="codex", prompt="x", worktree=str(tmp_path),
                    fake_script=str(p))
    r = invocar(inv, None)
    assert r.exit_code != 0
    assert r.failure_class == "CODEX_QUOTA"


def test_deteccao_de_cota_em_saidas_reais(cfg):
    for texto in ("You've hit your usage limit. Try again in 4 hours.",
                  "quota exceeded for this billing period",
                  "rate limit reached, retry after the window resets",
                  "insufficient credits to continue"):
        assert errors.classificar(texto, 1).value == "CODEX_QUOTA", texto
    # e NÃO é confundido com falha de código
    assert errors.classificar("AssertionError: 1 != 2", 1).value != "CODEX_QUOTA"


def test_sequencia_do_driver_fake_alterna(tmp_path, fake_agent):
    """A primeira chamada devolve cota; a segunda implementa. Base do teste de retomada."""
    from autodev.agents import _fake
    p = escreve_fake_spec(tmp_path / "seq.json", {"sequencia": [
        {"acao": "quota"},
        {"acao": "editar", "arquivo": "novo.txt", "conteudo": "feito"},
    ]})
    inv = Invocacao(agente="codex", prompt="x", worktree=str(tmp_path),
                    fake_script=str(p))
    a = _fake(inv)
    assert a.failure_class == "CODEX_QUOTA"
    b = _fake(inv)
    assert b.exit_code == 0
    assert (tmp_path / "novo.txt").read_text() == "feito"


# ---------------------------------------------------------------- T09 revisão
def test_revisao_cruzada_nunca_e_o_proprio_autor():
    disp = {"codex": agents.AgenteInfo(nome="codex", disponivel=True, binario="/x", versao="1.0"),
            "agy": agents.AgenteInfo(nome="agy", disponivel=True, binario="/y", versao="2.0"),
            "hermes": agents.AgenteInfo(nome="hermes", disponivel=True, binario="/z", versao="1.0")}
    assert review.escolher_revisor("codex", disp) == "agy"
    assert review.escolher_revisor("agy", disp) == "codex"


def test_revisor_cai_para_hermes_quando_cruzado_indisponivel():
    disp = {"codex": agents.AgenteInfo(nome="codex", disponivel=True, binario="/x", versao="1.0"),
            "agy": agents.AgenteInfo(nome="agy", disponivel=False, erro="ausente"),
            "hermes": agents.AgenteInfo(nome="hermes", disponivel=True, binario="/z", versao="1.0")}
    assert review.escolher_revisor("codex", disp) == "hermes"


def test_revisao_deterministica_reprova_segredo(repo):
    (repo / "cfg.py").write_text('TOKEN = "ghp_' + "a" * 30 + '"\n')
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "cfg com segredo", cwd=repo)
    r = review._revisao_deterministica(str(repo), "HEAD~1", ["sem segredos"],
                                       "1 passed", motivo="teste")
    assert not r.aprovado
    assert any("segredo" in f["descricao"].lower() for f in r.findings)


def test_revisao_deterministica_reprova_teste_quebrado(repo):
    r = review._revisao_deterministica(str(repo), "HEAD", ["x"],
                                       "2 failed, 1 passed", motivo="teste")
    assert not r.aprovado


def test_revisao_deterministica_aprova_caso_limpo(repo):
    (repo / "ok.py").write_text("def f():\n    return 1\n")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "codigo limpo", cwd=repo)
    r = review._revisao_deterministica(str(repo), "HEAD~1", ["x"], "3 passed",
                                       motivo="teste")
    assert r.aprovado and r.veredito == "APPROVE"
    assert not r.findings


def test_revisao_deterministica_reprova_task_sem_codigo(repo):
    """Task que não produziu alteração nenhuma não pode ser aprovada."""
    r = review._revisao_deterministica(str(repo), "HEAD", ["x"], "3 passed",
                                       motivo="teste")
    assert not r.aprovado


def test_parseia_veredito_json(tmp_path):
    txt = 'bla bla\n{"veredito":"REQUEST_CHANGES","findings":[{"severidade":"alta"}]}\nfim'
    d = review._extrai_json(txt)
    assert d and d["veredito"] == "REQUEST_CHANGES"


def test_revisao_usa_deterministica_quando_revisor_nao_esta_disponivel(repo, cfg):
    disp = {"codex": agents.AgenteInfo(nome="codex", disponivel=True, binario="/x", versao="1.0"),
            "agy": agents.AgenteInfo(nome="agy", disponivel=False, erro="ausente"),
            "hermes": agents.AgenteInfo(nome="hermes", disponivel=True, binario="/z", versao="1.0")}
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="1 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=disp)
    assert r.revisor == "hermes-deterministico"
    assert r.veredito in ("APPROVE", "REQUEST_CHANGES", "REJECT")


def test_revisao_parseia_veredito_do_revisor_cruzado(repo, cfg, monkeypatch):
    disp = {"codex": agents.AgenteInfo(nome="codex", disponivel=True, binario="/x", versao="1.0"),
            "agy": agents.AgenteInfo(nome="agy", disponivel=True, binario="/y", versao="2.0")}
    monkeypatch.setattr(review, "invocar", lambda inv, c: agents.Resultado(
        exit_code=0,
        stdout='{"veredito":"REQUEST_CHANGES","resumo":"falta tratamento",'
               '"findings":[{"severidade":"alta","arquivo":"a.py",'
               '"descricao":"sem validacao","sugestao":"validar"}]}'))
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="1 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=disp)
    assert r.revisor == "agy"
    assert not r.aprovado
    assert r.findings[0]["arquivo"] == "a.py"


# ---------------------------------------------------------------- T13 relatório
def test_relatorio_tem_as_16_secoes(store, cfg):
    store.criar_task(SPRINT, "T01", "detectar agentes")
    store.transicionar(SPRINT, "T01", "PLANNED")
    store.transicionar(SPRINT, "T01", "QUEUED")
    store.transicionar(SPRINT, "T01", "RUNNING")
    store.transicionar(SPRINT, "T01", "VERIFYING")
    store.transicionar(SPRINT, "T01", "REVIEW")
    store.transicionar(SPRINT, "T01", "DONE")
    txt = report.gerar(store, SPRINT, objetivo="obj", git_commit="abc")
    for secao in ("1. Resultado executivo", "2. Arquitetura implementada",
                  "3. Tasks concluídas", "4. Tasks bloqueadas", "5. Evidência de teste",
                  "6. Evidência do teste de aceitação",
                  "7. Findings de revisão de código", "8. Findings de segurança",
                  "9. Cota do Codex e recuperação",
                  "10. Uso de modelos e escalonamentos", "11. Retries e falhas",
                  "12. HAQ", "13. Desvios da arquitetura original",
                  "14. Dívida técnica", "15. Impacto esperado no HAR",
                  "16. Recomendação para o Sprint 2"):
        assert secao in txt, f"seção ausente no relatório: {secao}"


def test_relatorio_deriva_do_estado_e_nao_de_memoria(store):
    store.criar_task(SPRINT, "T01", "x")
    store.transicionar(SPRINT, "T01", "PLANNED")
    store.transicionar(SPRINT, "T01", "QUEUED")
    store.transicionar(SPRINT, "T01", "RUNNING")
    store.transicionar(SPRINT, "T01", "VERIFYING")
    store.transicionar(SPRINT, "T01", "REVIEW")
    store.transicionar(SPRINT, "T01", "DONE")
    store.criar_task(SPRINT, "T02", "y")
    txt = report.gerar(store, SPRINT)
    assert "T01" in txt and "T02" in txt
    assert "DONE" in txt


def test_relatorio_inclui_metricas_e_har(store):
    txt = report.gerar(store, SPRINT)
    for m in ("HAR", "tentativas", "escalonamentos", "esperas de cota", "HAQ"):
        assert m.lower() in txt.lower(), f"métrica ausente: {m}"


def test_escrever_relatorio_em_disco(store, tmp_path):
    txt = report.gerar(store, SPRINT)
    p = report.escrever(txt, tmp_path / "R.md")
    assert p.exists() and len(p.read_text()) == len(txt)


def test_relatorio_expoe_origem_da_conclusao(store):
    """Task concluida por evidencia nao pode parecer execucao do orquestrador.

    Sem isso o relatorio deixaria o leitor concluir que as 15 tasks sairam do
    laco autonomo — decisions.md D-13.
    """
    store.criar_task(SPRINT, "T01", "detectar agentes")
    store.concluir_task_evidenciada(SPRINT, "T01", evidencia="modulo existe + testes")
    txt = report.gerar(store, SPRINT)
    assert "origem" in txt.lower()
    assert "retroativo" in txt
    assert "**1** retroativo" in txt        # contagem no resultado executivo
    assert "Atenção" in txt                 # a ressalva esta presente


def test_relatorio_nao_alerta_quando_tudo_e_do_orquestrador(store):
    """A ressalva so aparece quando ha conclusao nao-executada."""
    store.criar_task(SPRINT, "T01", "x")
    store.transicionar(SPRINT, "T01", "PLANNED")
    store.transicionar(SPRINT, "T01", "QUEUED")
    store.transicionar(SPRINT, "T01", "RUNNING")
    store.transicionar(SPRINT, "T01", "VERIFYING")
    store.transicionar(SPRINT, "T01", "DONE")
    txt = report.gerar(store, SPRINT)
    assert "Atenção" not in txt
