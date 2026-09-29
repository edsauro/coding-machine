"""Contrato da documentacao publica do planejador."""

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"


def _corpo_da_secao(titulo: str) -> str:
    texto = README.read_text(encoding="utf-8")
    inicio = re.search(rf"^## {re.escape(titulo)}$", texto, re.MULTILINE)
    assert inicio, f"README não contém a seção {titulo!r} como título Markdown"
    proximo = re.search(r"^## ", texto[inicio.end():], re.MULTILINE)
    fim = len(texto) if proximo is None else inicio.end() + proximo.start()
    return texto[inicio.start():fim]


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
    texto = _corpo_da_secao("Portão do plano").lower()

    assert "portão do plano" in texto
    assert "planejar" in texto and "prever" in texto
    assert "aprovar" in texto and "rodar" in texto
    assert "arquivo de teste próprio" in texto
    assert "sem colisão de arquivo na mesma onda" in texto
    assert "critério de preservação" in texto
    assert "arquivo ou comando" in texto


def test_readme_documenta_verificador_e_estado_atual_das_sprints():
    portao = _corpo_da_secao("Portão do plano")
    estado_atual = _corpo_da_secao("Estado atual")

    assert ".autodev/scripts/verificar_plano.py" in portao
    assert "python3 .autodev/scripts/verificar_plano.py DEVFACTORY-003" in portao
    assert "python3 -m pytest .autodev/tests/ -q" in estado_atual
    assert not re.search(r"\b\d+\s*(?:testes?|tests?|passed|verdes)\b", estado_atual, re.I)
    assert re.search(r"DEVFACTORY-001`: ENCERRADO", estado_atual)
    assert re.search(r"DEVFACTORY-002`: EM EXECUÇÃO", estado_atual)
    assert re.search(r"DEVFACTORY-003`: PLANEJADO", estado_atual)


def test_exemplo_documentado_do_verificador_roda_sem_erros():
    resultado = subprocess.run(
        [sys.executable, ".autodev/scripts/verificar_plano.py", "DEVFACTORY-003"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert "OK    DEVFACTORY-003:" in resultado.stdout


def test_decisions_registra_aprovacao_humana_e_d16():
    decisoes = ROOT / ".autodev/sprints/DEVFACTORY-003/decisions.md"
    texto = decisoes.read_text(encoding="utf-8").lower()

    assert "aprovação humana" in texto
    assert "d-16" in texto
    assert "sprint 2" in texto
