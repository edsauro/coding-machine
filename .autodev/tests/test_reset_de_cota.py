"""A espera de cota deve usar o reset que o PRÓPRIO agente informa.

Contexto (28/09): a política fixa de 5h10m fez o motor dormir 5h por cima de um
reset de 31 min anunciado pelo codex ("try again at 9:10 AM") — e, na madrugada
anterior, 56 min a mais. A política continua sendo o TETO: o que o agente informa
só vale quando é menor, para que um parse absurdo (o agy anunciou 5 DIAS) não
estacione o sprint.

Todos os testes passam `agora` explícito: nada depende do relógio da máquina.
"""
import datetime as dt
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from autodev import errors  # noqa: E402

AGORA = dt.datetime(2026, 9, 28, 8, 40, 0).timestamp()
POLITICA = 18600          # 5h10m — o valor de produção


def _fmt(ts: float, formato: str = "%d/%m %H:%M") -> str:
    return dt.datetime.fromtimestamp(ts).strftime(formato)


# ------------------------------------------------------------- formas aceitas
def test_duracao_compacta_do_agy():
    """agy: "Resets in 120h58m47s" — 5 dias, então NÃO pode ser usada como espera."""
    r = errors.reset_de_cota("Individual quota reached. Resets in 120h58m47s", AGORA)
    assert r == pytest.approx(AGORA + 120 * 3600 + 58 * 60 + 47, abs=1)
    assert errors.espera_efetiva(POLITICA, "Resets in 120h58m47s", AGORA) == POLITICA


def test_duracao_por_extenso():
    assert errors.espera_efetiva(POLITICA, "Your usage limit resets in 5 hours.",
                                 AGORA) == 5 * 3600
    assert errors.espera_efetiva(POLITICA, "resets in 20 minutes", AGORA) == 1200
    assert errors.espera_efetiva(POLITICA, "resets in 45s", AGORA) == 45


def test_hora_absoluta_de_hoje():
    """codex: "try again at 9:10 AM" com agora = 08:40 → hoje, 31 min."""
    r = errors.reset_de_cota("You've hit your usage limit. try again at 9:10 AM.",
                             AGORA)
    assert _fmt(r) == "28/09 09:10"
    assert errors.espera_efetiva(POLITICA, "try again at 9:10 AM.", AGORA) == 1800


def test_hora_absoluta_ja_passada_vira_amanha():
    """Se a hora informada já passou hoje, o reset é amanhã — nunca no passado."""
    agora = dt.datetime(2026, 9, 28, 10, 0, 0).timestamp()
    r = errors.reset_de_cota("try again at 9:10 AM", agora)
    assert _fmt(r) == "29/09 09:10"


def test_data_com_ordinal_e_ano():
    """codex: "try again at Sep 28th, 2026 3:41 AM" (mensagem real desta madrugada)."""
    r = errors.reset_de_cota("... or try again at Sep 28th, 2026 3:41 AM.", AGORA)
    assert _fmt(r, "%d/%m/%Y %H:%M") == "28/09/2026 03:41"
    # já passou quando agora = 08:40 → a política assume (aqui sim, corretamente)
    assert errors.espera_efetiva(POLITICA, "try again at Sep 28th, 2026 3:41 AM.",
                                 AGORA) == POLITICA


def test_pm_e_meio_dia():
    assert _fmt(errors.reset_de_cota("try again at 3:41 PM", AGORA)) == "28/09 15:41"
    assert _fmt(errors.reset_de_cota("try again at 12:30 PM", AGORA)) == "28/09 12:30"
    # 00:30 já passou quando agora é 08:40 → o reset cai amanhã (nunca no passado)
    assert _fmt(errors.reset_de_cota("try again at 12:30 AM", AGORA)) == "29/09 00:30"


# -------------------------------------------------------------- formas ignoradas
@pytest.mark.parametrize("texto", [
    "", "tudo certo por aqui", None,
    "Error: cota esgotada sem hora", "you have 5 credits left",
    "AssertionError: 1 != 2",
])
def test_texto_sem_reset_legivel(texto):
    assert errors.reset_de_cota(texto, AGORA) is None
    assert errors.espera_efetiva(POLITICA, texto, AGORA) == POLITICA


def test_politica_e_teto():
    """Reset MAIOR que a política é ignorado a favor da política."""
    assert errors.espera_efetiva(POLITICA, "resets in 9 hours", AGORA) == POLITICA
    assert errors.espera_efetiva(POLITICA, "Resets in 6h", AGORA) == POLITICA


def test_piso_de_30s_contra_laco_apertado():
    """Reset de 5s é ruído: não pode fazer o motor bater na cota em laço."""
    assert errors.espera_efetiva(POLITICA, "resets in 5s", AGORA) == POLITICA


def test_reset_menor_que_politica_vence():
    assert errors.espera_efetiva(POLITICA, "try again at 8:55 AM", AGORA) == 900
    assert errors.espera_efetiva(8, "try again at 8:55 AM", AGORA) == 8, \
        "no modo_teste a política curta manda (determinismo do teste)"
