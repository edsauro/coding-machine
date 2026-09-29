"""Escalonamento de modelos, escada de revisão e desbloqueio (matriz 2026-09-27).

Cobre o que foi padronizado pelo autor:
  * escada de implementação: luna/low -> terra/low -> sol/low -> sol/medium -> astra/low;
  * escada de revisão: agy 3.6 -> 3.7 -> 3.8 Flash, depois Hermes flash e pro;
  * revisor de reserva quando o da tentativa não roda (cota do AGY esgotada);
  * piso determinístico: revisor LLM não aprova o que o portão objetivo reprova;
  * `desbloquear`: volta o sprint de FIM para EM_EXECUCAO e o contador de tentativas;
  * HAQ-001: credencial do Codex entra por --ro-bind, sem cópia dentro do projeto.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from autodev import agents, retry, review, sandbox

SOB_SANDBOX = os.environ.get("AUTODEV_SANDBOX") == "1"
SPRINT = "TESTE-900"


# ------------------------------------------------------- escada de implementação
def test_escada_de_implementacao_e_a_matriz_do_autor(cfg):
    esperado = [("gpt-5.6-luna", "low"), ("gpt-5.6-terra", "low"),
                ("gpt-5.6-sol", "low"), ("gpt-5.6-sol", "medium"),
                ("gpt-6-astra", "low")]
    obtido = [(cfg.modelo_para_tentativa(n)["slug"],
               cfg.modelo_para_tentativa(n)["effort"]) for n in range(1, 6)]
    assert obtido == esperado


def test_escada_de_implementacao_nao_estoura_o_teto_de_tentativas(cfg):
    maximo = cfg.policies["retry"]["max_tentativas_implementacao"]
    assert len(cfg.escada()) == maximo == 5


# ------------------------------------------------------------ escada de revisão
def test_escada_de_revisao_por_tentativa(cfg):
    esperado = [("agy", "Gemini 3.6 Flash (Low)"),
                ("agy", "Gemini 3.7 Flash (Low)"),
                ("agy", "Gemini 3.8 Flash (Low)"),
                ("hermes", "deepseek-flash"),
                ("hermes", "deepseek-v4-pro")]
    obtido = [(cfg.revisor_para_tentativa(n).get("agente"),
               cfg.revisor_para_tentativa(n).get("modelo")) for n in range(1, 6)]
    assert obtido == esperado


def test_revisor_de_reserva_existe_e_e_o_hermes_flash(cfg):
    assert cfg.revisor_reserva() == {"agente": "hermes", "modelo": "deepseek-flash"}


def test_cadeia_de_reserva_termina_no_hermes_pro(cfg):
    """Não parar: agy fora -> flash -> pro, e só então o portão determinístico."""
    assert [c["modelo"] for c in cfg.revisor_reserva_cadeia()] == [
        "deepseek-flash", "deepseek-v4-pro"]


def test_retomada_no_terceiro_degrau_usa_sol_low_e_agy_38(cfg):
    """Retomada da sprint 2: contador 2 => próxima tentativa é a 3ª da escada.

    Bug real da 1ª tentativa de retomada: o modelo da primeira volta do laço era
    fixo em `modelo_para_tentativa(1)` (luna/low), ignorando o contador da task.
    """
    prox = 2 + 1
    m = cfg.modelo_para_tentativa(prox)
    assert (m["slug"], m["effort"]) == ("gpt-5.6-sol", "low")
    assert cfg.revisor_para_tentativa(prox)["modelo"] == "Gemini 3.8 Flash (Low)"


def test_tentativa_fora_da_escada_usa_o_ultimo_degrau(cfg):
    """Revisor é o que menos pode faltar: além do 5º degrau, usa o 5º."""
    assert cfg.revisor_para_tentativa(9) == cfg.revisor_para_tentativa(5)
    assert cfg.modelo_para_tentativa(9)["slug"] == "gpt-6-astra"


def test_quem_implementa_nunca_revisa_nas_tentativas_hermes(cfg):
    """Codex implementa; nas tentativas 4 e 5 quem revisa é o Hermes, não o codex."""
    for n in (4, 5):
        assert cfg.revisor_para_tentativa(n)["agente"] == "hermes"


def _disp(**kwal) -> dict:
    base = {"codex": agents.AgenteInfo("codex", True, "1.0", "/x"),
            "agy": agents.AgenteInfo("agy", True, "2.0", "/y"),
            "hermes": agents.AgenteInfo("hermes", True, "1.0", "/z")}
    for k, v in kwal.items():
        base[k] = v
    return base


APROVADO = ('{"veredito":"APPROVE","confianca":0.9,"resumo":"ok",'
            '"findings":[],"criterios_atendidos":[]}')
REPROVADO = ('{"veredito":"REQUEST_CHANGES","confianca":0.8,"resumo":"falta",'
             '"findings":[{"severidade":"alta","arquivo":"a.py","descricao":"x"}]}')


def _captura(monkeypatch, resposta_por_agente: dict, chamadas: list | None = None):
    def fake(inv, cfg):
        if chamadas is not None:
            chamadas.append(inv)
        r = resposta_por_agente.get(inv.agente, resposta_por_agente.get("*"))
        if isinstance(r, agents.Resultado):
            return r
        return agents.Resultado(exit_code=0, stdout=r,
                                modelo_usado=inv.modelo or "(default)")
    monkeypatch.setattr(review, "invocar", fake)


def test_revisor_da_escada_e_usado_quando_a_tentativa_e_conhecida(repo, cfg,
                                                                 monkeypatch):
    chamadas: list = []
    _captura(monkeypatch, {"*": APROVADO}, chamadas)
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=3)
    assert r.revisor == "agy"
    assert r.modelo == "Gemini 3.8 Flash (Low)"
    assert r.origem.startswith("escada (tentativa 3)")
    assert chamadas[0].modelo == "Gemini 3.8 Flash (Low)"
    # effort NÃO é flag do agy: o rótulo já carrega o effort — mandar -e quebra
    assert chamadas[0].effort is None


def test_tentativa_4_e_5_revisam_com_hermes_e_prompt_somente_leitura(repo, cfg,
                                                                    monkeypatch):
    for tentativa, modelo in ((4, "deepseek-flash"), (5, "deepseek-v4-pro")):
        chamadas: list = []
        _captura(monkeypatch, {"*": REPROVADO}, chamadas)
        r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                           criterios=["c"], testes="3 passed", agente_impl="codex",
                           cfg=cfg, disponiveis=_disp(), tentativa=tentativa)
        assert (r.revisor, r.modelo) == ("hermes", modelo)
        assert chamadas[0].agente == "hermes"
        assert chamadas[0].edita is False
        assert "SOMENTE LEITURA" in chamadas[0].prompt
        assert r.veredito == "REQUEST_CHANGES"


def test_reserva_entra_quando_o_revisor_da_escada_falha(repo, cfg, monkeypatch):
    """Cota do AGY esgotada na 3ª tentativa: quem revisa é a reserva, registrada."""
    falha = agents.Resultado(exit_code=1, stderr="Error: quota exceeded",
                             failure_class="CODEX_QUOTA")
    chamadas: list = []
    _captura(monkeypatch, {"agy": falha, "hermes": REPROVADO}, chamadas)
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=3)
    assert r.revisor == "hermes"
    assert r.modelo == "deepseek-flash"
    assert r.origem.startswith("reserva")
    assert [c.agente for c in chamadas] == ["agy", "hermes"]
    # a revisão denuncia quem estourou cota, para o orquestrador tirar de circulação
    assert r.sem_cota == ["agy"]
    assert r.to_dict()["sem_cota"] == ["agy"]


def test_cadeia_de_reserva_atravessa_flash_e_pro_ate_revisar(repo, cfg,
                                                             monkeypatch):
    """agy fora, flash fora: quem revisa é o pro. O sprint não para por revisor."""
    falha = agents.Resultado(exit_code=1, stderr="Error: quota reached",
                             failure_class="CODEX_QUOTA")
    chamadas: list = []

    def fake(inv, cfg):
        chamadas.append((inv.agente, inv.modelo))
        if inv.modelo == "deepseek-v4-pro":
            return agents.Resultado(exit_code=0, stdout=REPROVADO,
                                    modelo_usado="deepseek-v4-pro")
        return falha

    monkeypatch.setattr(review, "invocar", fake)
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=3)
    assert chamadas == [("agy", "Gemini 3.8 Flash (Low)"),
                        ("hermes", "deepseek-flash"),
                        ("hermes", "deepseek-v4-pro")]
    assert (r.revisor, r.modelo) == ("hermes", "deepseek-v4-pro")
    assert r.origem.startswith("reserva")
    assert r.veredito == "REQUEST_CHANGES"


def test_revisao_nunca_para_o_sprint_quando_todos_os_llm_falham(repo, cfg,
                                                                monkeypatch):
    """Pior caso: todo revisor LLM falha -> portão determinístico decide."""
    falha = agents.Resultado(exit_code=1, stderr="unavailable",
                             failure_class="ENVIRONMENT_ERROR")
    monkeypatch.setattr(review, "invocar", lambda inv, c: falha)
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=4)
    assert r.revisor == "hermes-deterministico"
    assert r.veredito in ("APPROVE", "REQUEST_CHANGES", "REJECT")
    assert "esgotados" in r.resumo


def test_sem_escada_a_revisao_cruzada_continua(repo, cfg, monkeypatch):
    """Sem tentativa informada, vale a spec §16: codex implementa, agy revisa."""
    _captura(monkeypatch, {"*": REPROVADO})
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp())
    assert (r.revisor, r.origem) == ("agy", "cruzada")


def test_piso_deterministico_impede_aprovacao_sem_codigo(repo, cfg, monkeypatch):
    """Revisor LLM aprovou, mas o worktree não tem alteração nenhuma: reprova."""
    _captura(monkeypatch, {"*": APROVADO})
    r = review.revisar(worktree=str(repo), base="HEAD", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=4)
    assert r.veredito == "REQUEST_CHANGES"
    assert "piso deterministico" in r.resumo
    assert any("nenhuma alteracao" in f["descricao"] for f in r.findings)


def test_piso_nao_mexe_em_aprovacao_de_task_com_codigo(repo, cfg, monkeypatch):
    (repo / "novo.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "codigo"], cwd=repo, check=True)
    _captura(monkeypatch, {"*": APROVADO})
    r = review.revisar(worktree=str(repo), base="HEAD~1", task_id="T1", titulo="t",
                       criterios=["c"], testes="3 passed", agente_impl="codex",
                       cfg=cfg, disponiveis=_disp(), tentativa=5)
    assert r.aprovado and not r.findings


# ---------------------------------------------------------------- adaptador Hermes
def test_adaptador_hermes_monta_prompt_no_argumento(monkeypatch):
    visto: dict = {}

    def fake_run(cmd, **kw):
        visto["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="OK", stderr="")

    monkeypatch.setattr(agents.subprocess, "run", fake_run)
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)
    res = agents.invocar(agents.Invocacao(agente="hermes", prompt="revise isto",
                                          worktree="/tmp", modelo="deepseek-flash"),
                         cfg=None)
    assert visto["cmd"] == ["hermes", "-m", "deepseek-flash", "-z", "revise isto"]
    assert res.modelo_usado == "deepseek-flash"
    # flags que só existem no codex/agy não podem vazar para o hermes
    for flag in ("-d", "-e", "--timeout", "-f", "-s", "-t", "-D"):
        assert flag not in visto["cmd"]


# ------------------------------------------------------------------- desbloqueio
def _dag_teste() -> dict:
    return {"sprint_id": SPRINT, "tasks": [
        {"id": "P01", "titulo": "t1", "criterios": ["c1"]},
        {"id": "P02", "titulo": "t2", "criterios": ["c2"], "deps": ["P01"]}]}


def test_reabrir_tasks_devolve_estado_e_contador(store):
    store.criar_tasks_do_dag(SPRINT, _dag_teste())
    store.bloqueia(SPRINT, "P02", "limite de 5 tentativas")
    feitas = store.reabrir_tasks(SPRINT, ["P02"], tentativas=2,
                                 motivo="teste")
    assert feitas == ["P02"]
    row = store.conn.execute(
        "SELECT estado, tentativas, bloqueio FROM tasks WHERE sprint_id=?"
        " AND task_id='P02'", (SPRINT,)).fetchone()
    assert row["estado"] == "QUEUED" and row["tentativas"] == 2
    assert row["bloqueio"] is None
    ev = store.conn.execute(
        "SELECT payload FROM events WHERE sprint_id=? AND tipo='reaberto'"
        " ORDER BY id DESC LIMIT 1", (SPRINT,)).fetchone()
    assert ev is not None and "tentativas_antes" in ev["payload"]


def test_reabrir_sprint_tira_o_sprint_de_FIM(store):
    store.criar_tasks_do_dag(SPRINT, _dag_teste())
    store.checkpoint(SPRINT, "FIM", {"parado_por": None})
    assert store.estado_sprint(SPRINT) == "FIM"
    de = store.reabrir_sprint(SPRINT, motivo="teste")
    assert de == "FIM"
    assert store.estado_sprint(SPRINT) == "EM_EXECUCAO"


def test_desbloquear_recusa_contador_no_teto(cfg):
    """--tentativas >= maximo bloquearia na primeira checagem: a CLI recusa."""
    from autodev import cli
    import argparse
    args = argparse.Namespace(sprint=SPRINT, tasks=[], tentativas=5, motivo="")
    assert cli.cmd_desbloquear(args) == 1


# ------------------------------------------------------------------ HAQ-001
def test_prompt_de_task_proibe_perguntar_em_headless():
    """Sem humano do outro lado, pergunta de design não é entrega: o prompt diz isso."""
    from autodev.orchestrator import PROMPT_TASK
    assert "SEM HUMANO" in PROMPT_TASK
    assert "posso implementar" in PROMPT_TASK
    assert "FALHA da task" in PROMPT_TASK


def test_entrega_vazia_falha_a_tentativa_sem_gastar_revisao(tmp_path, monkeypatch):
    """Agente que responde e não altera arquivo: SEM_ENTREGA, e a revisão não roda.

    Caso real: o codex devolveu 'Voce aprova esse design para eu implementar?' e o
    motor tratou como sucesso (exit 0, stdout com texto). A suite pre-existente
    seguia verde, então a revisão foi gasta para descobrir que não havia entrega.
    """
    from autodev.orchestrator import Orquestrador
    from conftest import TASK_FIXTURE, criar_fixture, escreve_fake_spec, scaffold_sprint
    from test_acceptance import STATS_CORRIGIDO

    fx = criar_fixture(tmp_path / "fixture")
    # base VERDE: o cenario e "suite passa e mesmo assim nada foi entregue"
    (fx / "src" / "stats.py").write_text(STATS_CORRIGIDO, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=str(fx), capture_output=True)
    subprocess.run(["git", "commit", "-qm", "base verde"], cwd=str(fx), capture_output=True)

    scaffold_sprint(fx, SPRINT, [TASK_FIXTURE], objetivo="entrega vazia")
    spec = escreve_fake_spec(tmp_path / "fake.json", {
        "acao": "echo",
        "texto": "Você aprova esse design para eu implementar?",
    })
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(spec))

    o = Orquestrador(fx, SPRINT, modo_teste=True)
    o.rodar()

    tent = o.store.tentativas(SPRINT, "T01")
    assert tent, "a tentativa precisa ficar registrada"
    assert tent[0]["failure_class"] == "SEM_ENTREGA"
    assert all(t["review_result"] is None for t in tent), \
        "entrega vazia não pode consumir revisão"


def test_cota_do_agente_secundario_nao_estaciona_a_task(tmp_path, monkeypatch):
    """Regra "não parar": cota do agy devolve a tentativa e o codex reassume.

    Antes desta correção: a task era trocada para o agy (4ª tentativa) e, com a
    cota do agy estourada, ia para WAITING_RESOURCE com espera de 5h10m — parada
    por um agente que tem substituto. Aqui a prova é: nenhuma espera de cota fica
    registrada em nome do agy, a tentativa dele não consome o contador, e ele sai
    da rodada.
    """
    from autodev.orchestrator import Orquestrador
    from conftest import TASK_FIXTURE, criar_fixture, escreve_fake_spec, scaffold_sprint

    fx = criar_fixture(tmp_path / "fixture")
    scaffold_sprint(fx, SPRINT, [{**TASK_FIXTURE, "agente": "agy"}],
                    objetivo="cota do agente secundario")
    spec = escreve_fake_spec(tmp_path / "fake.json", {"acao": "quota"})
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(spec))

    o = Orquestrador(fx, SPRINT, modo_teste=True)
    o.rodar()

    assert "agy" in o._sem_cota, "o agy deveria sair da rodada"
    assert o.disponiveis["agy"].disponivel is False
    esperas = o.store.conn.execute(
        "SELECT agent FROM resource_waits WHERE sprint_id=?", (SPRINT,)).fetchall()
    assert all(e["agent"] == "codex" for e in esperas), \
        f"espera de cota em nome do agy: {[e['agent'] for e in esperas]}"
    devolvidas = o.store.conn.execute(
        "SELECT COUNT(*) c FROM attempts WHERE sprint_id=? AND"
        " failure_class='QUOTA_AGENTE'", (SPRINT,)).fetchone()["c"]
    assert devolvidas == 1, "a tentativa do agy precisa ficar registrada como devolvida"


@pytest.mark.skipif(SOB_SANDBOX, reason="prepara o HOME do host, não do sandbox")
def test_sandbox_nao_copia_a_credencial_para_dentro_do_projeto():
    home = sandbox.preparar_home()
    alvo = home / ".codex" / "auth.json"
    assert alvo.exists(), "o ponto de montagem precisa existir para o ro-bind"
    assert alvo.stat().st_size == 0, "nada de token copiado para dentro do projeto"


@pytest.mark.skipif(SOB_SANDBOX, reason="monta o comando do sandbox do host")
def test_sandbox_monta_a_credencial_somente_leitura(monkeypatch):
    if not sandbox.disponivel():
        pytest.skip("bwrap ausente")
    cmd = sandbox.montar_cmd(sandbox.SandboxSpec(worktree="/tmp"))
    if not (Path.home() / ".codex" / "auth.json").exists():
        pytest.skip("host sem credencial do Codex")
    i = cmd.index("/tmp/home/.codex/auth.json")
    assert cmd[i - 2] == "--ro-bind-try"
    assert cmd[i - 1] == str(Path.home() / ".codex" / "auth.json")
    # a cópia dentro do projeto não pode voltar a existir
    assert not (sandbox.RAIZ_HOME_SANDBOX / ".codex" / "auth.json").stat().st_size


@pytest.mark.skipif(SOB_SANDBOX, reason="limpa cópias no host")
def test_limpeza_remove_copia_antiga_em_worktree(tmp_path, monkeypatch):
    fake_raiz = tmp_path / "proj"
    copia = (fake_raiz / ".autodev" / "worktrees" / "S-T01-codex" / ".autodev"
             / "sandbox-home" / ".codex")
    copia.mkdir(parents=True)
    (copia / "auth.json").write_text('{"token":"segredo"}')
    monkeypatch.setattr(sandbox, "RAIZ_HOME_SANDBOX",
                        fake_raiz / ".autodev" / "sandbox-home")
    assert sandbox._limpar_copias_antigas() == [str(copia / "auth.json")]
    assert not (copia / "auth.json").exists()


# ------------------------------------------- P-09: infra não gasta degrau de modelo
def test_classe_declarada_nunca_escalona_modelo(cfg):
    """P-09 — a lista `classes_sem_escalonamento` da política tem de valer de fato.

    A intenção sempre esteve escrita; a fiação não existia: o degrau vinha do
    CONTADOR da task, então uma falha de rede/ambiente pagava a chamada cara.
    """
    for classe in sorted(cfg.classes_sem_escalonamento()):
        for n_tent in (1, 3, 4):
            d = retry.decidir(failure_class=classe, tentativas_implementacao=n_tent,
                              esperas_cota=0, agente_atual="codex", cfg=cfg,
                              fp_nova="mesma-falha", fps_anteriores=["mesma-falha"])
            assert d.estrategia != retry.Estrategia.ESCALONAR_MODELO, (
                f"{classe} na {n_tent}a tentativa escalou modelo — a política proíbe")
            if d.estrategia == retry.Estrategia.RETRY_IGUAL:
                atual = cfg.modelo_para_tentativa(n_tent)
                assert d.tier == atual["tier"], (
                    f"{classe}: a retomada tem de repetir o degrau ATUAL "
                    f"({atual['slug']}/{atual['effort']}), não o próximo da escada")


def test_orquestrador_respeita_o_degrau_da_decisao(cfg):
    """O motor não pode deduzir degrau do contador quando a decisão já diz qual é."""
    from autodev.orchestrator import Orquestrador
    orq = Orquestrador.__new__(Orquestrador)      # só o mapa decisão -> degrau
    orq.cfg = cfg
    # 4 tentativas feitas: o degrau ATUAL é o 4º (sol/medium), não o 5º (astra/low)
    d = retry.decidir(failure_class="NETWORK_ERROR", tentativas_implementacao=4,
                      esperas_cota=0, agente_atual="codex", cfg=cfg,
                      fp_nova="mesma-falha", fps_anteriores=["mesma-falha"])
    m = orq._modelo_da_tentativa(d, n_tent=4)
    assert (m["slug"], m["effort"]) == ("gpt-5.6-sol", "medium"), (
        "falha de rede não pode subir para o degrau caro (astra/low)")
    # e, sem decisão (1ª tentativa), o contador continua mandando
    m1 = orq._modelo_da_tentativa(None, n_tent=0)
    assert (m1["slug"], m1["effort"]) == ("gpt-5.6-luna", "low")
