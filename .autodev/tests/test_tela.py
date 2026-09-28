"""A tela de eventos (`tela.sh` / `.autodev/scripts/tela.py`).

Testa a tradução evento → uma linha curta, que é o que o autor lê. A função é pura
de propósito: nada aqui toca banco, então o teste não depende de estado real.
"""
import importlib.util
import sys
from pathlib import Path

CAMINHO = Path(__file__).resolve().parents[2] / ".autodev" / "scripts" / "tela.py"
spec = importlib.util.spec_from_file_location("tela", CAMINHO)
assert spec and spec.loader, f"nao consegui carregar {CAMINHO}"
tela = importlib.util.module_from_spec(spec)
sys.modules["tela"] = tela
spec.loader.exec_module(tela)


def _linha(tipo, payload=None, testes=None, revisao=None, task="P09"):
    r = tela.linha_do_evento(tipo, task, payload or {}, testes, revisao)
    return None if r is None else r[0]


# ------------------------------------------------- cobrança do autor (o essencial)
def test_inicio_de_codificacao_e_teste():
    txt = _linha("tentativa_iniciada",
                 {"attempt": 9, "agent": "codex", "model": "gpt-5.6-astra", "effort": "low"})
    assert txt.startswith("t9 codificação e teste iniciados")
    assert "codex gpt-5.6-astra low" in txt


def test_fim_de_desenvolvimento_com_testes_ok():
    txt = _linha("tentativa_finalizada", {"attempt": 8, "status": "OK"},
                 testes={"passed": 187, "failed": 0})
    assert "desenvolvimento e testes finalizados" in txt
    assert "187" in txt


def test_teste_de_pacote_falhou_com_contagem():
    txt = _linha("tentativa_finalizada",
                 {"attempt": 6, "status": "FAILED", "failure_class": "TEST_FAILURE"},
                 testes={"passed": 202, "failed": 1})
    assert "testes falharam" in txt and "202 ok" in txt and "1 com falha" in txt


def test_revisao_reprovada_mostra_revisor_e_achados():
    txt = _linha("tentativa_finalizada",
                 {"attempt": 8, "status": "FAILED", "failure_class": "REVIEW_FAILURE"},
                 revisao={"veredito": "REQUEST_CHANGES", "revisor": "hermes",
                          "findings": [{}, {}, {}]})
    assert "revisão reprovada" in txt and "hermes (3 achados)" in txt


def test_integracao_e_commit():
    txt = _linha("integrado", {"task_id": "P08", "merge_ok": True,
                               "commit": "a9445a39405ba7c"})
    assert txt == "integrada · commit a9445a39"


def test_cota_e_bloqueio():
    from datetime import datetime
    ts = 1790613000.0
    esperado = datetime.fromtimestamp(ts).strftime("%H:%M")   # sem depender do fuso
    assert f"aguardando cota até {esperado}" in _linha(
        "espera_recurso", {"retry_after": ts})
    assert "BLOQUEADA" in _linha("bloqueado", {"motivo": "limite de 5 tentativas"})


def test_fim_de_sprint():
    txt = _linha("sprint_encerrado", {"resultado": "CONCLUIDO",
                                      "resumo": "T01-T14 por evidencia retroativa"})
    assert txt.startswith("SPRINT CONCLUIDO")


def test_haq_aparece_como_dependencia_do_autor():
    assert "depende de você" in _linha("haq_criado", {"haq_id": "HAQ-002"})


# ------------------------------------------------------- ruído filtrado da tela
def test_transicao_rotineira_nao_polui_a_tela():
    assert _linha("transicao", {"de": "QUEUED", "para": "RUNNING"}) is None
    assert _linha("transicao", {"de": "RUNNING", "para": "WAITING_RESOURCE"}) is None


def test_transicao_relevante_aparece():
    assert "→ RETRY" in _linha("transicao", {"de": "RUNNING", "para": "RETRY"})


def test_evento_desconhecido_nao_quebra():
    txt = _linha("evento_novo_do_futuro", {"x": 1})
    assert "evento_novo_do_futuro" in txt


def test_payload_quebrado_nao_quebra():
    assert _linha("tentativa_finalizada", {"attempt": None, "status": None}) is not None
