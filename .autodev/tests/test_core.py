"""Testes do núcleo: persistência, transições, schemas, retry, HAQ, kill switch.

Cobre os itens da spec §25:
  persistência de task, transições de estado, lógica de retry, escalonamento de
  modelo, detecção de cota, agendamento de retomada 5h10m, HAQ, kill switch.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from autodev import errors, haq, killswitch, retry
from autodev.config import (Config, TRANSICOES, carrega_dag, ordem_topologica,
                            valida_dag)
from autodev.state import SprintNaoEncerravel, StateStore, TransicaoInvalida

SPRINT = "S1"


# ---------------------------------------------------------------- T02 schemas
def test_dag_valido_e_ondas():
    dag = {"tasks": [{"id": "A", "titulo": "a", "criterios": ["x"]},
                     {"id": "B", "titulo": "b", "criterios": ["y"], "deps": ["A"]},
                     {"id": "C", "titulo": "c", "criterios": ["z"], "deps": ["A"]}]}
    assert valida_dag(dag) == []
    ondas = ordem_topologica(dag)
    assert ondas == [["A"], ["B", "C"]]


def test_dag_rejeita_ciclo():
    dag = {"tasks": [{"id": "A", "titulo": "a", "criterios": ["x"], "deps": ["B"]},
                     {"id": "B", "titulo": "b", "criterios": ["y"], "deps": ["A"]}]}
    erros = valida_dag(dag)
    assert any("ciclo" in e for e in erros)


def test_dag_rejeita_dep_inexistente_e_id_duplicado():
    dag = {"tasks": [{"id": "A", "titulo": "a", "criterios": ["x"], "deps": ["Z"]},
                     {"id": "A", "titulo": "dup", "criterios": ["y"]}]}
    erros = valida_dag(dag)
    assert any("duplicados" in e for e in erros)
    assert any("não existe" in e for e in erros)


def test_dag_do_sprint_real_e_valido():
    p = Path(__file__).resolve().parent.parent / "sprints" / "DEVFACTORY-001" / "dag.json"
    dag = json.loads(p.read_text())
    assert valida_dag(dag) == []
    tasks = {t["id"] for t in dag["tasks"]}
    assert len(tasks) == 15, "o backlog exige T01..T15"


# ---------------------------------------------------------------- T03 persistência
def test_persistencia_sobrevive_a_reabertura(tmp_path):
    db = tmp_path / "s.db"
    with StateStore(db) as st:
        st.criar_task(SPRINT, "T1", "teste")
        st.transicionar(SPRINT, "T1", "PLANNED")
        st.iniciar_tentativa(SPRINT, "T1", agent="codex", model="gpt-5.6-luna",
                             effort="low", branch="b", worktree="w",
                             sandbox="bwrap", base_commit="abc")
    with StateStore(db) as st:
        t = st.task(SPRINT, "T1")
        assert t["estado"] == "PLANNED"
        assert len(st.tentativas(SPRINT, "T1")) == 1
        a = st.ultima_tentativa(SPRINT, "T1")
        assert a["model"] == "gpt-5.6-luna" and a["effort"] == "low"
        assert a["sandbox"] == "bwrap" and a["base_commit"] == "abc"


def test_criar_task_e_idempotente(store):
    for _ in range(3):
        store.criar_task(SPRINT, "T1", "x")
    assert len(store.tasks(SPRINT)) == 1


def test_attempt_persiste_todos_os_campos_exigidos(store):
    store.criar_task(SPRINT, "T1")
    store.iniciar_tentativa(SPRINT, "T1", agent="codex", model="m", effort="low",
                            branch="b", worktree="w", sandbox="s", base_commit="c")
    store.finalizar_tentativa(SPRINT, "T1", 1, status="OK", exit_code=0,
                              changed_files=["a.py"], test_result={"passou": True},
                              review_result={"veredito": "APPROVE"},
                              final_commit="deadbeef", failure_class=None,
                              fingerprint="fp1", retry_after=None)
    a = store.ultima_tentativa(SPRINT, "T1")
    exigidos = ["sprint_id", "task_id", "attempt", "agent", "model", "branch",
                "worktree", "sandbox", "base_commit", "start_time",
                "last_heartbeat", "status", "exit_code", "changed_files",
                "test_result", "review_result", "final_commit", "failure_class",
                "retry_after"]
    faltando = [c for c in exigidos if c not in a.keys()]
    assert not faltando, f"campos ausentes na tabela attempts: {faltando}"
    assert json.loads(a["changed_files"]) == ["a.py"]
    assert a["final_commit"] == "deadbeef"


# ---------------------------------------------------------------- transições
def test_transicao_valida_e_invalida(store):
    store.criar_task(SPRINT, "T1")
    store.transicionar(SPRINT, "T1", "PLANNED")
    store.transicionar(SPRINT, "T1", "QUEUED")
    store.transicionar(SPRINT, "T1", "RUNNING")
    store.transicionar(SPRINT, "T1", "VERIFYING")
    store.transicionar(SPRINT, "T1", "REVIEW")
    store.transicionar(SPRINT, "T1", "DONE")
    store.transicionar(SPRINT, "T1", "INTEGRATED")
    assert store.task(SPRINT, "T1")["estado"] == "INTEGRATED"
    with pytest.raises(TransicaoInvalida):
        store.transicionar(SPRINT, "T1", "RUNNING")


def test_todos_os_estados_existem_e_tem_transicoes_declaradas():
    esperados = {"NEW", "PLANNED", "QUEUED", "RUNNING", "VERIFYING", "BLOCKED",
                 "REVIEW", "RETRY", "DONE", "FAILED", "INTEGRATED",
                 "WAITING_RESOURCE"}
    assert esperados <= set(TRANSICOES)


def test_estado_forcado_para_recuperacao(store):
    store.criar_task(SPRINT, "T1")
    store.transicionar(SPRINT, "T1", "PLANNED")
    store.transicionar(SPRINT, "T1", "QUEUED")
    store.transicionar(SPRINT, "T1", "RUNNING")
    store.forcar_estado(SPRINT, "T1", "RETRY", "recuperado de crash")
    assert store.task(SPRINT, "T1")["estado"] == "RETRY"


# ---------------------------------------------------------------- T20 taxonomia
@pytest.mark.parametrize("texto,esperado", [
    ("Error: quota exceeded for this billing period", "CODEX_QUOTA"),
    ("usage limit reached, resets in 3 hours", "CODEX_QUOTA"),
    ("Permission denied: /etc/shadow", "PERMISSION_REQUIRED"),
    ("ModuleNotFoundError: No module named 'foo'", "DEPENDENCY_ERROR"),
    ("AssertionError: 1 != 2", "TEST_FAILURE"),
    ("Traceback (most recent call last):", "CODE_ERROR"),
    ("could not resolve host: api.openai.com", "NETWORK_ERROR"),
    ("No space left on device", "ENVIRONMENT_ERROR"),
    ("algo que nao casa com nada", "UNKNOWN"),
])
def test_classificacao_de_falhas(texto, esperado):
    assert errors.classificar(texto, 1).value == esperado


def test_classes_humanas_e_de_recurso():
    assert errors.e_humana(errors.FailureClass.PERMISSION_REQUIRED)
    assert errors.e_recurso(errors.FailureClass.CODEX_QUOTA)
    assert not errors.consome_tentativa(errors.FailureClass.CODEX_QUOTA)
    assert errors.consome_tentativa(errors.FailureClass.TEST_FAILURE)


# ---------------------------------------------------------------- T10 retry
def test_fingerprint_igual_para_mesma_falha():
    a = retry.fingerprint("T1", "TEST_FAILURE", "abc", ["x.py"], ["test_x"])
    b = retry.fingerprint("T1", "TEST_FAILURE", "abc", ["x.py"], ["test_x"])
    c = retry.fingerprint("T1", "TEST_FAILURE", "abc", ["y.py"], ["test_x"])
    assert a == b and a != c
    assert retry.similaridade(a, b) == 1.0


def test_mesmo_lugar_detecta_loop():
    fps = ["aaaaaaaaaaaaaaaaaaaa"]
    assert retry.mesmo_lugar("aaaaaaaaaaaaaaaaaaaa", fps)
    assert not retry.mesmo_lugar("bbbbbbbbbbbbbbbbbbbb", fps)


def test_escalonamento_sobe_um_degrau(cfg):
    m1 = cfg.modelo_para_tentativa(1)
    m2 = cfg.modelo_para_tentativa(2)
    m3 = cfg.modelo_para_tentativa(3)
    assert (m1["slug"], m1["effort"]) == ("gpt-5.6-luna", "low")
    assert m2["tier"] == 1 and m2["slug"] == "gpt-5.6-luna"
    assert m3["tier"] == 2, "a 3a tentativa sobe um degrau"
    assert m3["tier"] > m2["tier"] > m1["tier"]


def test_modelos_da_escada_existem_de_fato(cfg, tmp_path):
    """Nenhum nome de modelo inventado: a escada bate com o cache do Codex."""
    cache = Path.home() / ".codex" / "models_cache.json"
    if not cache.exists():
        pytest.skip("models_cache.json ausente")
    txt = cache.read_text()
    for degrau in cfg.escada():
        assert degrau["slug"] in txt, f"modelo inventado: {degrau['slug']}"
    esforcos = set(cfg.models["codex"]["efforts_validos"])
    for degrau in cfg.escada():
        assert degrau["effort"] in esforcos


def test_decisao_quota_nao_consome_tentativa(cfg):
    d = retry.decidir(failure_class="CODEX_QUOTA", tentativas_implementacao=2,
                      esperas_cota=0, agente_atual="codex", cfg=cfg)
    assert d.estrategia == retry.Estrategia.ESPERAR_RECURSO
    assert d.tentativa_proxima == 2, "espera de cota NAO incrementa tentativas"
    assert d.retry_after is not None


def test_agendamento_510h_em_producao_e_curto_em_teste(cfg):
    assert cfg.espera_cota(False) == 5 * 3600 + 10 * 60, "producao = 5h10m"
    assert cfg.espera_cota(True) < 60, "teste usa delay curto"
    agora = 1_000_000.0
    d = retry.decidir(failure_class="CODEX_QUOTA", tentativas_implementacao=1,
                      esperas_cota=0, agente_atual="codex", cfg=cfg, agora=agora)
    assert d.retry_after == agora + cfg.espera_cota(False)


def test_falha_ambiente_nao_escalona_modelo(cfg):
    d = retry.decidir(failure_class="DEPENDENCY_ERROR", tentativas_implementacao=1,
                      esperas_cota=0, agente_atual="codex", cfg=cfg)
    assert d.estrategia == retry.Estrategia.RETRY_IGUAL
    assert d.tier == 0, "erro de dependencia nao sobe de modelo"


def test_falha_humana_vira_haq(cfg):
    for cls in ("PERMISSION_REQUIRED", "SECRET_REQUIRED", "RED_ACTION_REQUIRED"):
        d = retry.decidir(failure_class=cls, tentativas_implementacao=1,
                          esperas_cota=0, agente_atual="codex", cfg=cfg)
        assert d.estrategia == retry.Estrategia.ABRIR_HAQ


def test_quarta_falha_repetida_troca_de_agente(cfg):
    fp = "f" * 20
    d = retry.decidir(failure_class="TEST_FAILURE", tentativas_implementacao=3,
                      esperas_cota=0, agente_atual="codex", cfg=cfg,
                      fp_nova=fp, fps_anteriores=[fp, fp, fp])
    assert d.estrategia == retry.Estrategia.TROCAR_AGENTE
    assert d.agente == "agy"


def test_quinta_falha_bloqueia(cfg):
    d = retry.decidir(failure_class="TEST_FAILURE", tentativas_implementacao=5,
                      esperas_cota=0, agente_atual="codex", cfg=cfg)
    assert d.estrategia == retry.Estrategia.BLOQUEAR


def test_prompt_de_retry_inclui_evidencia_nova(cfg):
    d = retry.decidir(failure_class="TEST_FAILURE", tentativas_implementacao=1,
                      esperas_cota=0, agente_atual="codex", cfg=cfg)
    p = retry.montar_prompt_retry("OBJETIVO", decisao=d,
                                  saida_testes="AssertionError: 1 != 2",
                                  arquivos=["a.py"],
                                  tentativas_anteriores=[{"attempt": 1,
                                                          "failure_class": "TEST_FAILURE"}])
    assert "AssertionError: 1 != 2" in p
    assert "EVIDENCIA NOVA" in p
    assert "NAO repita a abordagem anterior" in p


def test_prompt_de_continuacao_manda_continuar(cfg):
    p = retry.montar_prompt_continuacao(
        "OBJETIVO", task_id="T1", criterios=["a", "b"],
        estado_impl="mediana() ainda errada", arquivos=["src/stats.py"],
        ultimo_commit="abc123", testes_passando=["test_amplitude"],
        testes_falhando=["test_mediana"], tentativas=[{"attempt": 1,
                                                       "failure_class": "CODEX_QUOTA"}],
        findings=[{"severidade": "alta", "arquivo": "x", "descricao": "y"}],
        proxima_acao="corrigir mediana")
    assert "NAO RECOMECE" in p
    assert "CONTINUE" in p
    assert "abc123" in p and "src/stats.py" in p
    assert "test_mediana" in p


# ---------------------------------------------------------------- T11 HAQ
def test_haq_nao_duplica(store):
    args = dict(sprint_id=SPRINT, task_id="T1", reason="r", risk="k",
                dependencia="d", acao_humana="a", resultado="o", verificacao="v")
    assert store.haq_adicionar("HAQ-001", **args) is True
    assert store.haq_adicionar("HAQ-001", **args) is False
    assert len(store.haq_listar(SPRINT)) == 1


def test_haq_md_tem_os_sete_campos(tmp_path, store):
    haq.abrir(store, SPRINT, task_id="T1", reason="precisa sudo",
              risk="alto", dependencia="task T1", acao_humana="sudo systemctl start x",
              resultado="serviço no ar", verificacao="systemctl is-active x")
    p = haq.escrever(store, SPRINT, tmp_path / "HAQ.md")
    txt = p.read_text()
    for campo in ("Task:", "Reason:", "Risk:", "Dependency:",
                  "Exact human action:", "Expected result:",
                  "How Hermes verifies completion:"):
        assert campo in txt, f"campo ausente no HAQ.md: {campo}"


def test_haq_por_falha_gera_item_com_comando(store, cfg):
    hid, novo = haq.abrir_por_falha(store, SPRINT, task_id="T1",
                                    failure_class="PERMISSION_REQUIRED",
                                    detalhe="cannot read /etc/x", cfg=cfg)
    assert novo and hid.startswith("HAQ-")
    assert len(store.haq_listar(SPRINT)) == 1


# ---------------------------------------------------------------- T26 kill switch
def test_killswitch_hierarquico(store):
    store.killswitch_set("STOP_SPRINT", True, alvo="S1")
    assert store.killswitch_ativo("S1") == "STOP_SPRINT"
    assert store.killswitch_ativo("S2") is None
    store.killswitch_set("STOP_ALL", True)
    assert store.killswitch_ativo("S9") == "STOP_ALL"


def test_killswitch_por_arquivo(tmp_path, store):
    killswitch.ativar(tmp_path, "STOP_ALL", "emergencia")
    assert killswitch.ler_flag(tmp_path)[0] == "STOP_ALL"
    with pytest.raises(killswitch.ParadoPorKillSwitch):
        killswitch.verificar(store, tmp_path, sprint="S1", acao="invocar_agente")
    killswitch.desativar(tmp_path)
    killswitch.verificar(store, tmp_path, sprint="S1", acao="invocar_agente")


def test_killswitch_stop_task_nao_afeta_outras(store):
    store.killswitch_set("STOP_TASK", True, alvo="T1")
    assert store.killswitch_ativo("S1", "T1") == "STOP_TASK"
    assert store.killswitch_ativo("S1", "T2") is None


# ---------------------------------------------------------------- checkpoint
def test_checkpoint_sobrevive(store):
    store.criar_task(SPRINT, "T1")
    store.checkpoint(SPRINT, "WAITING_RESOURCE", {"task_id": "T1", "attempt": 1}, 2)
    c = store.ler_checkpoint(SPRINT)
    assert c["estado"] == "WAITING_RESOURCE"
    assert c["checkpoint"]["task_id"] == "T1"
    assert c["ultima_onda"] == 2


# ------------------------------------------ ciclo de vida do sprint (encerrar)
def test_sprint_nao_tem_estado_ate_ser_registrado(store):
    """O `status:` do sprint.yaml e declaracao; o fato mora no banco."""
    assert store.estado_sprint(SPRINT) is None


def test_transicao_de_sprint_valida_e_invalida(store):
    store.transicionar_sprint(SPRINT, "EM_EXECUCAO", "inicio")
    assert store.estado_sprint(SPRINT) == "EM_EXECUCAO"
    store.transicionar_sprint(SPRINT, "EM_VERIFICACAO", "tasks concluidas")
    assert store.estado_sprint(SPRINT) == "EM_VERIFICACAO"
    with pytest.raises(TransicaoInvalida):
        store.transicionar_sprint(SPRINT, "PLANEJADO")


def test_encerrar_recusa_com_task_fora_de_estado_terminal(store):
    store.criar_task(SPRINT, "T1")
    with pytest.raises(SprintNaoEncerravel) as e:
        store.encerrar_sprint(SPRINT)
    assert "T1(NEW)" in str(e.value)
    assert store.estado_sprint(SPRINT) != "ENCERRADO"


def test_encerrar_apos_concluir_as_tasks(store):
    for t in ("T1", "T2"):
        store.criar_task(SPRINT, t)
        store.concluir_task_evidenciada(SPRINT, t, evidencia="modulo existe")
    assert store.pendentes(SPRINT) == []
    store.encerrar_sprint(SPRINT, resultado="CONCLUIDO", resumo="tudo verde")
    assert store.estado_sprint(SPRINT) == "ENCERRADO"
    c = store.ler_checkpoint(SPRINT)["checkpoint"]
    assert c["resultado"] == "CONCLUIDO" and c["resumo"] == "tudo verde"
    # o caminho tem de estar no historico: a maquina de estados vale tambem
    # para quem esta encerrando (historico vem do mais recente para o mais antigo)
    estados = [h["estado"] for h in store.historico_checkpoints(SPRINT)][::-1]
    assert estados == ["EM_EXECUCAO", "EM_VERIFICACAO", "ENCERRADO"], estados
    # e o passo final nao pode estar gravado duas vezes
    assert estados.count("ENCERRADO") == 1


def test_encerrar_duas_vezes_e_recusado(store):
    store.criar_task(SPRINT, "T1")
    store.concluir_task_evidenciada(SPRINT, "T1", evidencia="e")
    store.encerrar_sprint(SPRINT)
    with pytest.raises(SprintNaoEncerravel):
        store.encerrar_sprint(SPRINT)


def test_encerrar_forcado_registra_as_pendentes(store):
    store.criar_task(SPRINT, "T1")
    store.encerrar_sprint(SPRINT, resultado="ABORTADO", forcar=True)
    assert store.estado_sprint(SPRINT) == "ENCERRADO"
    c = store.ler_checkpoint(SPRINT)["checkpoint"]
    assert c["forcado"] is True
    assert c["tasks_pendentes_no_encerramento"] == ["T1(NEW)"]


# -------------------------------------------- conclusao por evidencia (rota b)
def test_evidencia_percorre_caminho_valido_ate_done(store):
    store.criar_task(SPRINT, "T1")
    att = store.concluir_task_evidenciada(SPRINT, "T1", evidencia="x.py + 12 testes")
    assert att == 1
    assert store.task(SPRINT, "T1")["estado"] == "DONE"
    assert store.ultima_tentativa(SPRINT, "T1")["status"] == "OK"


def test_evidencia_marca_origem_retroativo(store):
    """Ninguem deve confundir isto com execucao real do orquestrador."""
    store.criar_task(SPRINT, "T1")
    store.concluir_task_evidenciada(SPRINT, "T1", evidencia="x")
    assert store.ultima_tentativa(SPRINT, "T1")["origem"] == "retroativo"


def test_evidencia_de_task_inexistente_falha(store):
    with pytest.raises(KeyError):
        store.concluir_task_evidenciada(SPRINT, "NOPE", evidencia="x")


def test_evidencia_recusa_task_ja_concluida(store):
    store.criar_task(SPRINT, "T1")
    store.concluir_task_evidenciada(SPRINT, "T1", evidencia="x")
    with pytest.raises(ValueError):
        store.concluir_task_evidenciada(SPRINT, "T1", evidencia="x")


def test_evidencia_parte_de_estado_intermediario_sem_voltar_atras(store):
    store.criar_task(SPRINT, "T1")
    store.transicionar(SPRINT, "T1", "PLANNED")
    store.transicionar(SPRINT, "T1", "QUEUED")
    store.concluir_task_evidenciada(SPRINT, "T1", evidencia="x")
    assert store.task(SPRINT, "T1")["estado"] == "DONE"


def test_evidencia_registra_evento_auditavel(store):
    store.criar_task(SPRINT, "T1")
    store.concluir_task_evidenciada(SPRINT, "T1", evidencia="relatorio.pdf")
    ev = [e for e in store.eventos(SPRINT)
          if e["tipo"] == "task_concluida_por_evidencia"]
    assert len(ev) == 1
    assert json.loads(ev[0]["payload"])["evidencia"] == "relatorio.pdf"


# ------------------------------------------------------------------- migracao
def test_migracao_adiciona_coluna_origem(tmp_path):
    """Banco criado por versao anterior nao tem attempts.origem."""
    db = tmp_path / "legado.db"
    with StateStore(db) as st:
        st.criar_task(SPRINT, "T1")
        st.iniciar_tentativa(SPRINT, "T1", agent="codex", model="m", effort="low",
                             branch="b", worktree="w", sandbox="s", base_commit="c")
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE attempts DROP COLUMN origem")
    conn.commit()
    conn.close()
    with StateStore(db) as st:
        assert st.ultima_tentativa(SPRINT, "T1")["origem"] == "orquestrador"


def test_colunas_declaradas_le_o_proprio_ddl():
    """A reconciliacao le o SCHEMA; nao ha segunda lista de colunas a mao."""
    from autodev.state import _colunas_declaradas
    col = _colunas_declaradas()
    for t in ("tasks", "attempts", "events", "haq", "sprint_state"):
        assert t in col, f"{t} nao foi reconhecida no SCHEMA"
    assert "failure_class" in [n for n, _ in col["haq"]]
    nomes_tasks = [n for n, _ in col["tasks"]]
    assert "sprint_id" in nomes_tasks
    assert not any(n.upper().startswith("PRIMARY") for n in nomes_tasks), \
        "linha de constraint foi confundida com coluna"


def test_migracao_generica_adiciona_coluna_faltante(tmp_path):
    """Regressao: coluna nova no SCHEMA nao aparece em banco antigo.

    Foi assim que `haq.failure_class` quebrou num state.db real: os testes
    passavam (banco novo) e o banco de verdade falhava com
    'table haq has no column named failure_class'.
    """
    db = tmp_path / "legado.db"
    with StateStore(db) as st:
        st.criar_task(SPRINT, "T1")
    conn = sqlite3.connect(str(db))
    conn.execute("ALTER TABLE haq DROP COLUMN failure_class")
    conn.commit()
    conn.close()
    with StateStore(db) as st:
        colunas = {r["name"] for r in st.conn.execute("PRAGMA table_info(haq)")}
        assert "failure_class" in colunas, "migracao nao reconciliou a coluna"
        # e o HAQ volta a funcionar de fato
        novo = st.haq_adicionar(
            "H1", sprint_id=SPRINT, task_id="T1", reason="r", risk="alto",
            dependencia="d", acao_humana="a", resultado="x", verificacao="v",
            failure_class="PERMISSION_REQUIRED")
        assert novo is True
        assert st.haq_listar(SPRINT)[0]["failure_class"] == "PERMISSION_REQUIRED"

