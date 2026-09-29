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


def test_readme_alerta_sobre_agente_e_revisao_humana_sem_cota_incorreta():
    secao = _secao_planejador().lower()

    assert "agente" in secao
    assert "consome cota do agente configurado" not in secao
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


def test_readme_documenta_portao_e_checklist_derivado_do_d16():
    texto = README.read_text(encoding="utf-8").lower()

    assert "portão do plano" in texto
    assert "planejar" in texto and "prever" in texto
    assert "aprovar" in texto and "rodar" in texto
    assert "arquivo de teste próprio" in texto
    assert "sem colisão de arquivo na mesma onda" in texto
    assert "critério de preservação" in texto
    assert "arquivo ou comando" in texto


def test_readme_documenta_verificador_e_estado_atual_das_sprints():
    texto = README.read_text(encoding="utf-8")

    assert ".autodev/scripts/verificar_plano.py" in texto
    assert "verificar_plano.py DEVFACTORY-003" in texto
    assert "325" in texto or "testes" in texto.lower()
    for sprint in ("DEVFACTORY-001", "DEVFACTORY-002", "DEVFACTORY-003"):
        assert sprint in texto


def test_decisions_registra_aprovacao_humana_e_d16():
    decisoes = README.parent / ".autodev/sprints/DEVFACTORY-003/decisions.md"
    texto = decisoes.read_text(encoding="utf-8").lower()

    assert "aprovação humana" in texto
    assert "d-16" in texto
    assert "sprint 2" in texto
