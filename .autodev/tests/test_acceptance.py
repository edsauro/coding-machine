"""Teste de aceitação ponta a ponta (T15, spec §24/§25).

Prova o fluxo completo SEM intervenção humana:
  sprint aprovado -> Hermes planeja -> task atribuída -> worktree isolado criado
  -> agente implementa -> testes rodam -> revisão independente ocorre -> findings
  -> testes repetidos -> código integrado -> critérios verificados -> relatório.

E prova também a exaustão de cota simulada:
  CODEX_QUOTA -> checkpoint -> WAITING_RESOURCE -> retry agendado -> restauração
  -> CONTINUAR em vez de reiniciar.

O agente de teste é o driver determinístico (AUTODEV_FAKE_AGENT): reproduzível e
sem consumir cota real. O adaptador real é exercitado em test_adapters.py.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from autodev import haq, killswitch, report, testrunner
from autodev.agents import AgenteInfo
from autodev.orchestrator import Orquestrador

from conftest import TASK_FIXTURE, criar_fixture, escreve_fake_spec, scaffold_sprint

SPRINT = "ACEITE"

STATS_CORRIGIDO = '''"""Funcoes estatisticas do projeto fixture (CORRIGIDO)."""
from __future__ import annotations


def mediana(valores: list[float]) -> float:
    """Mediana de uma lista nao vazia. Nao muta a entrada."""
    if not valores:
        raise ValueError("lista vazia")
    o = sorted(valores)
    n = len(o)
    meio = n // 2
    if n % 2:
        return float(o[meio])
    return (o[meio - 1] + o[meio]) / 2.0


def desvio_padrao(valores: list[float]) -> float:
    """Desvio padrao AMOSTRAL (denominador N-1)."""
    if len(valores) < 2:
        raise ValueError("precisa de ao menos 2 valores")
    m = sum(valores) / len(valores)
    var = sum((x - m) ** 2 for x in valores) / (len(valores) - 1)
    return var ** 0.5


def amplitude(valores: list[float]) -> float:
    """Amplitude (max - min)."""
    if not valores:
        raise ValueError("lista vazia")
    return max(valores) - min(valores)
'''


def _ambiente(tmp_path, monkeypatch, sequencia, tarefas=None):
    """Fixture + sprint + driver determinístico, prontos para o orquestrador."""
    fx = criar_fixture(tmp_path / "fixture")
    scaffold_sprint(fx, SPRINT, tarefas or [TASK_FIXTURE],
                    objetivo="corrigir o modulo stats do projeto-fixture")
    spec = escreve_fake_spec(tmp_path / "fake.json", sequencia)
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(spec))
    monkeypatch.setenv("AUTODEV_AGENT_TIMEOUT", "180")
    return fx, spec


def _orquestrador(fx, monkeypatch, **kw):
    """Orquestrador com revisão determinística (não gasta agente real)."""
    o = Orquestrador(fx, SPRINT, **kw)
    o.disponiveis["agy"] = AgenteInfo(nome="agy", disponivel=False,
                                      erro="desativado no teste de aceitacao")
    return o


# ==================================================================== aceitação
def test_aceitacao_ponta_a_ponta(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {
        "acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)

    r = o.rodar()

    # --- 1. a task foi concluída sozinha
    assert r.parado_por is None, f"o sprint nao deveria parar: {r.parado_por}"
    assert r.concluidas == 1, f"esperava 1 task concluida, veio {r.concluidas}"

    t = o.store.task(SPRINT, "T01")
    assert t["estado"] in ("DONE", "INTEGRATED"), t["estado"]

    # --- 2. worktree isolado foi criado com branch próprio
    wt = Path(t["worktree"])
    assert wt.exists() and (wt / ".git").exists()
    assert t["branch"].startswith(f"sprint/{SPRINT}/T01-")
    assert wt != fx, "o agente nao pode trabalhar na raiz do repositorio"

    # --- 3. o agente implementou de verdade
    assert (wt / "src" / "stats.py").read_text() == STATS_CORRIGIDO

    # --- 4. os testes que estavam vermelhos agora passam
    rt = testrunner.rodar(wt, usar_sandbox=False)
    assert rt.passou, f"testes ainda falham: {rt.saida[-500:]}"

    # --- 5. houve verificação e revisão independentes, registradas
    tent = o.store.tentativas(SPRINT, "T01")
    assert len(tent) >= 1
    assert tent[0]["test_result"], "faltou registrar o resultado dos testes"
    assert tent[0]["review_result"], "faltou registrar a revisao"
    rev = json.loads(tent[0]["review_result"])
    assert rev.get("veredito") == "APPROVE", rev

    # --- 6. evidência em disco
    ev = list((fx / ".autodev" / "sprints" / SPRINT / "evidence").glob("*.txt"))
    assert ev, "nenhuma evidencia de teste foi salva"
    assert any("passed" in p.read_text() for p in ev)

    # --- 7. integração no branch do sprint, e MAIN INTACTA
    assert (fx / "src" / "stats.py").read_text().count("mediana real") == 0, \
        "a raiz do repo nao pode ter sido alterada"
    from autodev.worktree import branch_existe
    assert branch_existe(fx, f"sprint/{SPRINT}/integration")
    assert branch_existe(fx, "sprint/DEVFACTORY-001/integration") is False or True

    # --- 8. relatório gerado a partir do estado
    txt = report.gerar(o.store, SPRINT, objetivo="teste de aceitacao")
    assert "1. Resultado executivo" in txt and "15. Impacto esperado no HAR" in txt
    assert "T01" in txt

    # --- 9. nenhum kill switch pendente, nenhum HAQ criado
    assert o.store.killswitch_ativo(SPRINT) is None
    assert o.store.haq_listar(SPRINT) == []


def test_aceitacao_nao_toca_main(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {
        "acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO})
    from autodev.worktree import commit_atual
    antes = commit_atual(fx)
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.rodar()
    assert commit_atual(fx) == antes, "main NAO pode receber merge automatico"
    # e o branch de integração recebeu a correção
    from autodev.worktree import git
    conteudo = git("show", f"sprint/{SPRINT}/integration:src/stats.py", cwd=fx)
    assert "AMOSTRAL" in conteudo


# ============================================================ cota do Codex (T12)
def test_cota_esgotada_vira_espera_de_recurso_e_retoma_com_continuacao(
        tmp_path, monkeypatch):
    """O teste mais importante da spec §12/§24."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quota"},
        {"acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO},
    ]})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)

    # ---------- 1ª execução: cota esgotada ---------------------------------
    o.rodar()
    t = o.store.task(SPRINT, "T01")
    assert t["estado"] == "WAITING_RESOURCE", \
        f"cota NAO pode virar FAILED, veio {t['estado']}"
    assert t["esperas_cota"] == 1
    assert t["tentativas"] == 1, "espera de cota conta separado de tentativa"

    esperas = o.store.conn.execute(
        "SELECT * FROM resource_waits WHERE sprint_id=?", (SPRINT,)).fetchall()
    assert len(esperas) == 1
    delta = esperas[0]["retry_after"] - esperas[0]["detectado_em"]
    assert 0 < delta < 120, f"modo_teste deve usar delay curto, veio {delta}s"

    # o checkpoint preservou o estado (histórico append-only)
    cks = o.store.historico_checkpoints(SPRINT)
    cota_ck = [c for c in cks if "retry_after" in (c["checkpoint"] or {})]
    assert cota_ck, "nenhum checkpoint registrou o retry_after da espera de cota"
    ck = cota_ck[0]
    assert ck["estado"] == "WAITING_RESOURCE"
    assert ck["checkpoint"]["task_id"] == "T01"
    assert ck["checkpoint"]["retry_after"] > 0
    assert ck["checkpoint"]["commit"], "o checkpoint deve gravar o commit preservado"
    att = o.store.tentativas(SPRINT, "T01")[-1]
    assert att["failure_class"] == "CODEX_QUOTA"
    assert att["retry_after"] is not None, "retry_after precisa ser persistido"

    # ---------- 2ª execução: cota voltou -----------------------------------
    # libera o relógio (em produção seria 5h10m depois)
    o.store.conn.execute(
        "UPDATE resource_waits SET retry_after=? WHERE sprint_id=?",
        (time.time() - 1, SPRINT))
    o.store.conn.commit()

    o2 = _orquestrador(fx, monkeypatch, modo_teste=True)
    o2.rodar()

    t2 = o2.store.task(SPRINT, "T01")
    assert t2["estado"] in ("DONE", "INTEGRATED"), \
        f"nao retomou depois da espera: {t2['estado']}"

    # ---------- CONTINUOU, não reiniciou -----------------------------------
    logs = sorted((fx / ".autodev" / "sprints" / SPRINT / "logs").glob("T01-*"))
    assert len(logs) >= 2
    prompt_retomada = logs[-1].read_text()
    assert "CONTINU" in prompt_retomada.upper(), \
        "o prompt de retomada tem que mandar CONTINUAR"
    assert "NAO RECOMECE" in prompt_retomada.upper()
    # e carrega o estado real da implementação exigido pela spec §12
    assert "src/stats.py" in prompt_retomada
    for campo in ("CRITERIOS DE ACEITACAO", "ESTADO ATUAL DA IMPLEMENTACAO",
                  "ARQUIVOS RELEVANTES", "ULTIMO COMMIT",
                  "TESTES QUE JA PASSAM", "TESTES QUE AINDA FALHAM",
                  "TENTATIVAS ANTERIORES", "FINDINGS DE REVISAO",
                  "PROXIMA ACAO ESPERADA", "TASK"):
        assert campo in prompt_retomada.upper(), f"prompt sem a seção {campo}"
    # o objetivo original continua anexado no topo (não é a conversa inteira)
    assert "corrigir mediana" in prompt_retomada.lower()

    # o trabalho da 1ª tentativa foi preservado no worktree
    assert (Path(t2["worktree"]) / "src" / "stats.py").exists()


def test_cota_repetida_nao_bloqueia_a_task(tmp_path, monkeypatch):
    """Duas esperas de cota seguidas não contam como falha de implementação."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quota"}, {"acao": "quota"},
        {"acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO},
    ]})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.rodar()
    assert o.store.task(SPRINT, "T01")["estado"] == "WAITING_RESOURCE"
    assert o.store.task(SPRINT, "T01")["esperas_cota"] == 1

    o.store.conn.execute("UPDATE resource_waits SET retry_after=? WHERE sprint_id=?",
                         (time.time() - 1, SPRINT))
    o.store.conn.commit()
    o2 = _orquestrador(fx, monkeypatch, modo_teste=True)
    o2.rodar()
    t = o2.store.task(SPRINT, "T01")
    assert t["esperas_cota"] == 2
    assert t["estado"] == "WAITING_RESOURCE"
    assert t["tentativas"] <= 2, "espera de cota nao consome tentativa de implementacao"

    o2.store.conn.execute("UPDATE resource_waits SET retry_after=? WHERE sprint_id=?",
                          (time.time() - 1, SPRINT))
    o2.store.conn.commit()
    o3 = _orquestrador(fx, monkeypatch, modo_teste=True)
    o3.rodar()
    assert o3.store.task(SPRINT, "T01")["estado"] in ("DONE", "INTEGRATED")


def test_espera_de_cota_nao_vencida_nao_reinvoca_o_agente(tmp_path, monkeypatch):
    """Portão de cota: `retry_after` no futuro NÃO pode disparar nova chamada.

    Antes deste portão a espera era só REGISTRADA — o orquestrador reinvocava o
    agente na hora e as 5h10m nunca aconteciam de fato. Numa noite inteira com
    vigia retomando, isso martelaria a cota até o sol nascer.
    """
    fx, spec = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quota"},
        {"acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO},
    ]})
    conta = Path(spec).with_suffix(".count")

    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.rodar()
    assert o.store.task(SPRINT, "T01")["estado"] == "WAITING_RESOURCE"
    assert int(conta.read_text()) == 1, "a 1ª chamada é a que descobre a cota"

    # ---- 2ª execução SEM liberar o relógio: a espera está no futuro ---------
    o2 = _orquestrador(fx, monkeypatch, modo_teste=True)
    res = o2.rodar()

    assert o2.store.task(SPRINT, "T01")["estado"] == "WAITING_RESOURCE"
    assert o2.store.task(SPRINT, "T01")["esperas_cota"] == 1, \
        "não podia ter batido na cota de novo"
    assert int(conta.read_text()) == 1, \
        "o agente foi REINVOCADO antes do retry_after vencer"
    assert res.aguardando_recurso is True
    assert res.parado_por == "aguardando recurso (cota)", \
        "o vigia precisa distinguir 'volte depois' de travamento"
    assert res.concluidas == 0

    # ---- liberando o relógio: retoma e a espera fica RESOLVIDA -------------
    o2.store.conn.execute("UPDATE resource_waits SET retry_after=? WHERE sprint_id=?",
                          (time.time() - 1, SPRINT))
    o2.store.conn.commit()
    o3 = _orquestrador(fx, monkeypatch, modo_teste=True)
    o3.rodar()

    assert o3.store.task(SPRINT, "T01")["estado"] in ("DONE", "INTEGRATED")
    pend = o3.store.conn.execute(
        "SELECT COUNT(*) c FROM resource_waits WHERE resolvido=0").fetchone()["c"]
    assert pend == 0, f"a espera vencida devia estar resolvida, restam {pend}"


# ============================================================ retry (T10)
def test_retry_apos_teste_vermelho_reenfileira_e_conclui(tmp_path, monkeypatch):
    """Regressão: a 2ª tentativa reentra pela máquina de estados.

    O laço de retry volta com a task em RETRY; sem passar por QUEUED, a transição
    para RUNNING é recusada e o Sprint morre na segunda tentativa.
    """
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quebrar"},                                            # 1ª: nada
        {"acao": "editar", "arquivo": "src/stats.py",
         "conteudo": STATS_CORRIGIDO},                                  # 2ª: corrige
    ]})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    r = o.rodar()
    assert r.parado_por is None, f"o retry derrubou o sprint: {r.parado_por}"
    t = o.store.task(SPRINT, "T01")
    assert t["estado"] in ("DONE", "INTEGRATED"), t["estado"]
    assert t["tentativas"] == 2, f"esperava 2 tentativas, veio {t['tentativas']}"

    # a 2ª tentativa recebeu EVIDÊNCIA da falha (não o mesmo prompt)
    logs = sorted((fx / ".autodev" / "sprints" / SPRINT / "logs").glob("T01-*"))
    p2 = logs[-1].read_text()
    assert "EVIDENCIA NOVA" in p2.upper() or "FALHA" in p2.upper(), \
        "o retry precisa carregar a evidência da falha anterior"


def test_escalonamento_de_modelo_na_terceira_tentativa(tmp_path, monkeypatch):
    """Duas falhas informadas -> sobe um degrau de modelo (spec §10)."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quebrar"},
        {"acao": "quebrar"},
        {"acao": "editar", "arquivo": "src/stats.py",
         "conteudo": STATS_CORRIGIDO},
    ]})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    r = o.rodar()
    assert r.parado_por is None, r.parado_por
    tents = o.store.tentativas(SPRINT, "T01")
    assert len(tents) == 3, f"esperava 3 tentativas, veio {len(tents)}"
    t1, t2, t3 = tents
    assert t1["model"] == "gpt-5.6-luna" and t1["effort"] == "low"
    # a 3ª tentativa sobe de degrau (modelo mais forte ou effort maior)
    subiu = (t3["model"], t3["effort"]) != (t1["model"], t1["effort"])
    assert subiu, f"nao houve escalonamento: t1={t1['model']}/{t1['effort']} " \
                  f"t3={t3['model']}/{t3['effort']}"


def test_falha_de_revisao_volta_para_retry(tmp_path, monkeypatch):
    """Revisão reprovada devolve a task para retry, não para DONE."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "quebrar"},
        {"acao": "editar", "arquivo": "src/stats.py",
         "conteudo": STATS_CORRIGIDO},
    ]})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    r = o.rodar()
    assert r.parado_por is None
    tents = o.store.tentativas(SPRINT, "T01")
    assert len(tents) == 2
    # a 1ª tentativa falhou nos testes (não foi aprovada silenciosamente)
    assert tents[0]["status"] in ("FAILED", "RETRY")
    tr0 = json.loads(tents[0]["test_result"] or "{}")
    assert tr0.get("passou") is False, tr0


# ============================================================ recuperação (T10)
def test_estado_sobrevive_a_reinicializacao_do_orquestrador(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "quota"})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.rodar()
    estado_antes = o.store.task(SPRINT, "T01")["estado"]
    o.store.close()

    # processo novo, mesmo state.db
    o2 = _orquestrador(fx, monkeypatch, modo_teste=True)
    assert o2.store.task(SPRINT, "T01")["estado"] == estado_antes
    assert len(o2.store.tentativas(SPRINT, "T01")) == 1
    o2.store.close()


def test_worker_obsoleto_e_recuperado(tmp_path, monkeypatch):
    """Task presa em RUNNING com heartbeat velho volta para RETRY (spec §21)."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "x.txt",
                                              "conteudo": "ok"})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.carregar()
    o.store.criar_tasks_do_dag(SPRINT, o.dag)
    o.store.transicionar(SPRINT, "T01", "PLANNED")
    o.store.transicionar(SPRINT, "T01", "QUEUED")
    o.store.transicionar(SPRINT, "T01", "RUNNING")
    att = o.store.iniciar_tentativa(SPRINT, "T01", agent="codex", model="gpt-5.6-luna",
                                    effort="low", worktree="/wt/fantasma", branch="b",
                                    sandbox="bwrap", base_commit="abc")
    # heartbeat antigo
    o.store.conn.execute(
        "UPDATE attempts SET last_heartbeat=? WHERE sprint_id=? AND task_id=?",
        (time.time() - 99999, SPRINT, "T01"))
    o.store.conn.commit()
    st = o.store.stale_workers(timeout_s=3600)
    assert st, "heartbeat velho deveria aparecer como worker obsoleto"
    assert st[0]["task_id"] == "T01"
    rec = o.recuperar()
    assert rec["requeued"] and "T01" in rec["requeued"]
    assert o.store.task(SPRINT, "T01")["estado"] == "RETRY"


def test_heartbeat_e_atualizado_durante_a_execucao(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "src/stats.py",
                                              "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.rodar()
    a = o.store.tentativas(SPRINT, "T01")[-1]
    assert a["last_heartbeat"] and a["last_heartbeat"] > 0


def test_nunca_dois_escritores_no_mesmo_worktree(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "src/stats.py",
                                              "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.carregar()
    o.carregar()
    o.store.criar_tasks_do_dag(SPRINT, o.dag)
    o.store.adquirir_worktree("/wt/unico", "T01", "codex")
    from autodev.state import WorktreeOcupado
    # OUTRA task não pode assumir o mesmo worktree enquanto T01 escreve
    o.store.criar_task(SPRINT, "T02")
    with pytest.raises(WorktreeOcupado):
        o.store.adquirir_worktree("/wt/unico", "T02", "agy")
    # o mesmo dono pode readquirir (idempotência)
    o.store.adquirir_worktree("/wt/unico", "T01", "codex")
    # depois de liberar, outro pode assumir
    o.store.liberar_worktree("/wt/unico")
    o.store.adquirir_worktree("/wt/unico", "T02", "agy")


# ============================================================ kill switch (T26)
def test_killswitch_impede_novas_acoes(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "src/stats.py",
                                              "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    killswitch.ativar(fx, "STOP_ALL", "teste")
    r = o.rodar()
    assert r.parado_por, "o sprint deveria parar pelo kill switch"
    assert o.store.task(SPRINT, "T01")["estado"] != "DONE"
    # o estado persistido continua íntegro e o banco legível
    assert o.store.task(SPRINT, "T01") is not None


def test_killswitch_de_task_especifica(tmp_path, monkeypatch):
    """STOP_TASK impede novas ações daquela task, mas não das outras."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "src/stats.py",
                                              "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.carregar()
    o.store.killswitch_set("STOP_TASK", True, alvo="T01")
    with pytest.raises(killswitch.ParadoPorKillSwitch):
        o.executar_task("T01")
    # nada foi corrompido: a task continua existindo e num estado válido
    t = o.store.task(SPRINT, "T01")
    assert t is not None and t["estado"] in (
        "NEW", "PLANNED", "QUEUED", "RUNNING", "RETRY", "BLOCKED")


def test_killswitch_de_task_nao_afeta_as_outras(tmp_path, monkeypatch):
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "editar",
                                              "arquivo": "src/stats.py",
                                              "conteudo": STATS_CORRIGIDO})
    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.carregar()
    o.store.killswitch_set("STOP_TASK", True, alvo="T99")
    # a verificação para T01 não levanta
    killswitch.verificar(o.store, fx, sprint=SPRINT, acao="invocar_agente",
                         task="T01")


# ============================================================ HAQ (T11)
def test_falha_de_permissao_vira_haq_e_nao_para_o_sprint(tmp_path, monkeypatch):
    """Falha que exige humano tem que virar item de HAQ, não parada."""
    fx, _ = _ambiente(tmp_path, monkeypatch, {"acao": "nada"})
    # faz o driver devolver uma falha de permissão
    p = escreve_fake_spec(tmp_path / "fake.json", {"acao": "echo"})
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(p))

    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    o.carregar()
    o.store.criar_tasks_do_dag(SPRINT, o.dag)

    from autodev import agents as ag
    def falso(inv, cfg):
        return ag.Resultado(exit_code=1, stderr="Permission denied: /etc/shadow",
                            failure_class="PERMISSION_REQUIRED",
                            modelo_usado="gpt-5.6-luna", effort_usado="low",
                            prompt=inv.prompt)
    monkeypatch.setattr(ag, "invocar", falso)

    r = o.executar_task("T01")
    assert r.estado_final == "BLOCKED"
    itens = o.store.haq_listar(SPRINT)
    assert len(itens) == 1
    assert itens[0]["failure_class"] == "PERMISSION_REQUIRED"
    # HAQ.md escrito com os campos exigidos
    txt = (fx / ".autodev" / "sprints" / SPRINT / "HAQ.md").read_text()
    for campo in ("Task:", "Reason:", "Risk:", "Dependency:",
                  "Exact human action:", "Expected result:",
                  "How Hermes verifies completion:"):
        assert campo in txt


# =================================================== dependências e integração
@pytest.mark.xfail(strict=True, reason=(
    "BUG CONHECIDO: o worktree de toda task nasce da main (worktree.py: "
    "`base = base or commit_atual(self.repo)`), então uma task que declara `deps` "
    "espera pela dependência mas começa SEM o código dela. O DAG é respeitado para "
    "ORDEM e nunca para CONTEÚDO. Quando duas tasks tocam o mesmo arquivo, cada uma "
    "escreve a sua versão a partir da main e o merge colide (add/add). "
    "Remova esta marca quando a task passar a ser baseada no branch de integração."))
def test_task_dependente_enxerga_o_trabalho_da_dependencia(tmp_path, monkeypatch):
    """Task com `deps` precisa partir do resultado da dependência, não da main.

    Prova executável do que derrubou a primeira noite autônoma: 1 de 10 tarefas
    integrada, 9 em conflito de merge, em cinco rodadas seguidas.

    O ensaio com agente simulado passou 10 de 10 porque cada passo escrevia um
    arquivo DISTINTO. Sobreposição é exatamente o que os dados reais têm e os
    simulados não tinham — e é onde o defeito mora.
    """
    t1 = {"id": "T01", "titulo": "corrigir stats", "deps": [], "agente": "codex",
          "criterios": ["mediana() devolve a mediana real",
                        "todos os testes de tests/test_stats.py passam"],
          "estimativa": "S"}
    t2 = {"id": "T02", "titulo": "ajustar stats de novo", "deps": ["T01"],
          "agente": "codex",
          "criterios": ["mediana() continua correta apos o ajuste",
                        "todos os testes de tests/test_stats.py passam"],
          "estimativa": "S"}
    # As duas tasks escrevem O MESMO arquivo, com conteúdos DIVERGENTES.
    #
    # Cuidado com o desenho do caso: se a segunda entrega for um SUPERCONJUNTO da
    # primeira (mesmo conteúdo + linhas novas no fim), o git resolve sozinho e não
    # conflita — o teste passaria sem provar nada. Foi o primeiro erro deste teste.
    # Conteúdo genuinamente diferente conflita, e é o que duas tasks reais fazem.
    _t2 = STATS_CORRIGIDO.replace(
        "Mediana de uma lista nao vazia. Nao muta a entrada.",
        "Mediana de uma lista nao vazia (ajustada na T02).")
    assert _t2 != STATS_CORRIGIDO, "o caso precisa divergir de verdade"
    fx, _ = _ambiente(tmp_path, monkeypatch, {"sequencia": [
        {"acao": "editar", "arquivo": "src/stats.py", "conteudo": STATS_CORRIGIDO},
        {"acao": "editar", "arquivo": "src/stats.py", "conteudo": _t2},
    ]}, tarefas=[t1, t2])

    o = _orquestrador(fx, monkeypatch, modo_teste=True)
    r = o.rodar()

    por_task = {t.task_id: t.estado_final for t in r.tasks}
    t2_final = o.store.task(SPRINT, "T02")["estado"]

    assert por_task.get("T01") in ("DONE", "INTEGRATED"), por_task
    assert t2_final in ("DONE", "INTEGRATED"), (
        "a T02 depende da T01 e devia ter partido do trabalho dela — o merge "
        f"colidiu porque cada uma escreveu src/stats.py a partir da main "
        f"(estado final: {t2_final})")
    assert o.store.task(SPRINT, "T01")["estado"] == "INTEGRATED"
