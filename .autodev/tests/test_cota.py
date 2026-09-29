"""Testes da leitura das janelas de cota (P-14).

O caso que originou isto: 29/09/2026 01:50, janela de 5h em ~30% e semanal em 97% — o
motor esperou 5h10m (a política) e ia acordar, falhar e re-esperar até o reset da semanal.
"""
import time

import pytest

from autodev import cota

AGORA = 1790000000.0


def _janelas(p5h: int, semana: int) -> dict:
    return {
        "primary": {"usedPercent": p5h, "windowDurationMins": 300,
                    "resetsAt": AGORA + 3600},
        "secondary": {"usedPercent": semana, "windowDurationMins": 10080,
                      "resetsAt": AGORA + 5 * 86400},
    }


def test_escolhe_a_janela_mais_cheia():
    """Semanal estourada + 5h folgada ⇒ a espera mira o reset da SEMANAL, não os 5h."""
    j = cota.janela_estourada(_janelas(30, 97))
    assert j["nome"] == "secondary"
    assert j["usedPercent"] == 97


def test_espera_usa_o_reset_da_janela_certa_com_margem():
    seg, motivo = cota.espera_sugerida(_janelas(30, 97), agora=AGORA, margem_s=120)
    assert seg == pytest.approx(5 * 86400 + 120), seg
    assert "7d" in motivo and "97%" in motivo, motivo


def test_estourada_na_5h_espera_pouco():
    seg, motivo = cota.espera_sugerida(_janelas(100, 12), agora=AGORA, margem_s=60)
    assert seg == pytest.approx(3660), seg
    assert "5h" in motivo, motivo


def test_nada_acima_do_piso_nao_sugere_espera():
    """Nenhuma janela no limite: quem manda é o prazo da política (comportamento antigo)."""
    assert cota.janela_estourada(_janelas(12, 40)) is None
    assert cota.espera_sugerida(_janelas(12, 40), agora=AGORA) is None


def test_reset_no_passado_nao_sugere_espera():
    """Relógio do servidor atrás: melhor cair no prazo da política do que travar horas.

    (Reset dentro da margem ainda devolve espera curta — é para isso que a margem serve.)
    """
    j = {"secondary": {"usedPercent": 99, "windowDurationMins": 10080,
                       "resetsAt": AGORA - 7200}}
    assert cota.espera_sugerida(j, agora=AGORA) is None


def test_leitura_ausente_ou_quebrada_devolve_none():
    """Cota indisponível não pode derrubar o motor."""
    assert cota.janela_estourada(None) is None
    assert cota.janela_estourada({}) is None
    assert cota.espera_sugerida({}, agora=AGORA) is None


def test_rotulo_por_duracao():
    assert "5h" in cota._rotulo({"windowDurationMins": 300, "usedPercent": 100})
    assert "7d" in cota._rotulo({"windowDurationMins": 10080, "usedPercent": 97})
    assert "30min" in cota._rotulo({"windowDurationMins": 30, "usedPercent": 100})
