"""Contrato da documentacao publica do planejador."""

from pathlib import Path
import re


README = Path(__file__).parents[2] / "README.md"


def _secao_planejador() -> str:
    texto = README.read_text(encoding="utf-8")
    marcador = "## Planejador"
    assert marcador in texto
    inicio = texto.index(marcador) + len(marcador)
    restante = texto[inicio:]
    proximo_titulo = re.search(r"\n#{1,6} ", restante)
    fim = len(restante) if proximo_titulo is None else proximo_titulo.start()
    return " ".join(restante[:fim].split())


def test_readme_explica_fluxo_do_prompt_ao_sprint_executavel():
    secao = _secao_planejador().lower()

    assert "prompt" in secao
    assert "spec.md" in secao
    assert "dag.json" in secao
    assert "sprint.yaml" in secao
    assert "sprint executável" in secao


def test_readme_documenta_comando_plan_e_efeito_no_disco():
    secao = _secao_planejador()

    assert 'autodev plan "' in secao
    assert ".autodev/sprints/" in secao


def test_readme_alerta_sobre_agente_cota_e_revisao_humana():
    secao = _secao_planejador().lower()

    assert "agente" in secao
    assert "consome cota" in secao
    assert "revisão humana" in secao
    assert "sempre" in secao
    assert "antes de rodar" in secao


def test_readme_declara_que_plan_nao_faz_merge_nem_push():
    secao = _secao_planejador().lower()

    assert "não faz merge nem push" in secao


def test_secao_planejador_exclui_subsecoes_do_assunto_seguinte(monkeypatch):
    texto = """# Documento

## Planejador

Texto próprio do planejador.

### Ciclo de vida do sprint

Texto de outro assunto.

## Outro assunto

Mais texto.
"""
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: texto)

    secao = _secao_planejador()

    assert "Texto próprio do planejador." in secao
    assert "Ciclo de vida do sprint" not in secao
    assert "Texto de outro assunto." not in secao
