"""Estratégias de alocação de modelo (P-15, decisão do autor em 29/09/2026).

A escada de 5 degraus não era o único modelo possível. O autor definiu a "3 degraus"
— `luna/low -> astra/low -> luna/low` — e, com ela, uma regra de convivência: a task que
já entrou termina na escada com que começou. Estes testes fixam as duas coisas.
"""
import pytest

from autodev.config import Config

SPRINT = "DEVFACTORY-TESTE-ESTRATEGIAS"


# ------------------------------------------------------------------ as estratégias
def test_tres_degraus_e_luna_astra_luna(cfg: Config):
    """A estratégia do autor: sobe direto ao topo no 2º degrau e volta ao barato no 3º."""
    degraus = [(cfg.modelo_para_tentativa(n, "tres_degraus")["slug"],
                cfg.modelo_para_tentativa(n, "tres_degraus")["effort"])
               for n in (1, 2, 3)]
    assert degraus == [("gpt-5.6-luna", "low"), ("gpt-6-astra", "low"),
                       ("gpt-5.6-luna", "low")]
    assert cfg.max_tentativas("tres_degraus") == 3
    assert cfg.pede_confirmacao("tres_degraus") is True


def test_escada_5_continua_valendo_para_quem_entrou_com_ela(cfg: Config):
    """Task antiga NÃO muda de escada quando a padrão muda — é o pedido do autor."""
    assert (cfg.modelo_para_tentativa(2, "escada_5")["slug"]) == "gpt-5.6-terra"
    assert (cfg.modelo_para_tentativa(4, "escada_5")["slug"]) == "gpt-6-astra"
    assert cfg.max_tentativas("escada_5") == 5
    assert cfg.pede_confirmacao("escada_5") is False


def test_sem_estrategia_cai_no_legado(cfg: Config):
    """`None` = sem estratégia: relatório, CLI e testes antigos seguem na escada de 5."""
    assert (cfg.modelo_para_tentativa(2)["slug"]) == "gpt-5.6-terra"
    assert cfg.max_tentativas(None) == 5
    assert cfg.pede_confirmacao(None) is False


def test_estrategia_desconhecida_nao_quebra_o_motor(cfg: Config):
    """Banco/sprint velho pedindo estratégia que não existe cai no legado, não levanta."""
    assert cfg.max_tentativas("estrategia-que-nao-existe") == 5
    assert cfg.estrategia("estrategia-que-nao-existe")["nome"].endswith("(legado)")


def test_estrategia_padrao_e_a_do_autor(cfg: Config):
    assert cfg.estrategia_padrao() == "tres_degraus"


# ------------------------------------------------------ a regra de convivência
def test_task_gravada_uma_vez_nao_troca_de_estrategia(store):
    """Quem entrou na escada_5 termina na escada_5, mesmo com a padrão já sendo outra."""
    store.criar_task(SPRINT, "T1")
    assert store.definir_estrategia(SPRINT, "T1", "escada_5") == "escada_5"
    assert store.definir_estrategia(SPRINT, "T1", "tres_degraus") == "escada_5"
    # e quem não tem nenhuma recebe a que o motor ofereceu agora
    store.criar_task(SPRINT, "T2")
    assert store.definir_estrategia(SPRINT, "T2", "tres_degraus") == "tres_degraus"


def test_troca_de_estrategia_e_do_autor(store):
    """`--estrategia` troca, sim: é decisão explícita, não descuido de configuração."""
    store.criar_task(SPRINT, "T3")
    store.definir_estrategia(SPRINT, "T3", "escada_5")
    store.trocar_estrategia(SPRINT, "T3", "tres_degraus")
    assert store.definir_estrategia(SPRINT, "T3", "escada_5") == "tres_degraus"


def test_task_que_ja_tentou_nasce_marcada_como_escada_5(store, tmp_path):
    """A migração marca quem JÁ tentou: é o que preserva o sprint em curso (P-15).

    A task nova (zero tentativas) fica sem estratégia e pega a padrão na largada.
    """
    store.criar_task(SPRINT, "T4")
    store.criar_task(SPRINT, "T5")
    store.conn.execute("UPDATE tasks SET tentativas=2 WHERE sprint_id=? AND task_id='T4'",
                       (SPRINT,))
    store.conn.execute("UPDATE tasks SET estrategia=NULL WHERE sprint_id=?", (SPRINT,))
    store._migrar()          # reabrir o banco é o que roda a migração
    t4 = store.task(SPRINT, "T4")
    t5 = store.task(SPRINT, "T5")
    assert t4["estrategia"] == "escada_5"
    assert t5["estrategia"] is None


# ------------------------------------------------------------- o portão humano
def test_ultimo_degrau_da_estrategia_com_portao_para_em_waiting_human(store):
    """3 degraus sem resolver não é BLOCKED: é pergunta ao autor (WAITING_HUMAN)."""
    store.criar_task(SPRINT, "T6")
    for de, para in (("NEW", "PLANNED"), ("PLANNED", "QUEUED"),
                     ("QUEUED", "RUNNING"), ("RUNNING", "RETRY")):
        store.transicionar(SPRINT, "T6", para)
    store.aguarda_humano(SPRINT, "T6", "estrategia 'tres_degraus': 3 degraus sem resolver")
    assert store.task(SPRINT, "T6")["estado"] == "WAITING_HUMAN"
    assert store.metricas(SPRINT)["waiting_human"] == 1
    # o evento é o rastro que o vigia usa para avisar no Telegram
    tipos = [r["tipo"] for r in store.conn.execute(
        "SELECT tipo FROM events WHERE sprint_id=? AND task_id='T6'", (SPRINT,))]
    assert "confirmacao_pedida" in tipos


def test_waiting_human_volta_para_a_fila(store):
    """WAITING_HUMAN -> QUEUED é o caminho de resposta do autor (repetir/mudar)."""
    store.criar_task(SPRINT, "T7")
    for de, para in (("NEW", "PLANNED"), ("PLANNED", "QUEUED"),
                     ("QUEUED", "WAITING_HUMAN")):
        store.transicionar(SPRINT, "T7", para)
    store.reabrir_tasks(SPRINT, ["T7"], tentativas=0, motivo="autor: repetir")
    t = store.task(SPRINT, "T7")
    assert (t["estado"], t["tentativas"]) == ("QUEUED", 0)


def test_transicao_invalida_segue_barrada(store):
    """A máquina de estados continua sendo máquina: nada de pular de INTEGRATED."""
    store.criar_task(SPRINT, "T8")
    from autodev.state import TransicaoInvalida
    with pytest.raises(TransicaoInvalida):
        store.transicionar(SPRINT, "T8", "WAITING_HUMAN")
