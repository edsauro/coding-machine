#!/usr/bin/env python3
"""Cria (ou recria) o projeto-fixture de aceitação — T14.

O fixture é um repositório git independente com uma feature delimitada que
COMEÇA QUEBRADA, para provar o ciclo TDD vermelho->verde:
  - tests/test_stats.py tem 3 testes; 1 passa e 2 falham (mediana errada,
    desvio-padrão populacional em vez de amostral).
  - a task de aceitação é corrigir `mediana()` e `desvio_padrao()`.

O fixture é descartável: rodá-lo de novo recria do zero.
Uso: python3 .autodev/fixtures/criar_fixture.py [destino]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
DESTINO_PADRAO = RAIZ / "projeto-fixture"

STATS = '''"""Funcoes estatisticas do projeto fixture.

ATENCAO: `mediana` e `desvio_padrao` estao ERRADAS de proposito. Corrigir e a
task de aceitacao do Sprint DEVFACTORY-001.
"""
from __future__ import annotations


def mediana(valores: list[float]) -> float:
    """Mediana de uma lista nao vazia.

    BUG: retorna a media em vez da mediana.
    """
    if not valores:
        raise ValueError("lista vazia")
    return sum(valores) / len(valores)


def desvio_padrao(valores: list[float]) -> float:
    """Desvio padrao de uma amostra.

    BUG: usa o denominador populacional (N) em vez do amostral (N-1).
    """
    if len(valores) < 2:
        raise ValueError("precisa de ao menos 2 valores")
    m = sum(valores) / len(valores)
    var = sum((x - m) ** 2 for x in valores) / len(valores)
    return var ** 0.5


def amplitude(valores: list[float]) -> float:
    """Amplitude (max - min). Esta funcao esta CORRETA."""
    if not valores:
        raise ValueError("lista vazia")
    return max(valores) - min(valores)
'''

TESTES = '''"""Testes do modulo stats.

Estado inicial do fixture: `amplitude` passa; `mediana` e `desvio_padrao` falham.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from stats import amplitude, desvio_padrao, mediana  # noqa: E402


def test_amplitude_ja_funciona():
    assert amplitude([1, 5, 3]) == 4


def test_mediana_com_numero_impar_de_elementos():
    # a media seria 3.0; a mediana correta e 3
    assert mediana([1, 2, 3, 4, 5]) == 3
    # a media seria 4.0; a mediana correta e 4
    assert mediana([1, 9, 4]) == 4


def test_mediana_com_numero_par_de_elementos():
    # a media seria 3.75; a mediana correta e a media dos dois centrais = 3.5
    assert mediana([1, 2, 5, 7]) == 3.5


def test_mediana_nao_muta_a_entrada():
    dados = [3, 1, 2]
    mediana(dados)
    assert dados == [3, 1, 2]


def test_mediana_lista_vazia_levanta_erro():
    with pytest.raises(ValueError):
        mediana([])


def test_desvio_padrao_amostral():
    # amostral (N-1) = 1.0 ; populacional (N) = 0.816...
    assert desvio_padrao([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, abs=1e-4)


def test_desvio_padrao_amostral_simples():
    assert desvio_padrao([1, 3]) == pytest.approx(1.41421, abs=1e-4)


def test_desvio_padrao_precisa_de_dois_valores():
    with pytest.raises(ValueError):
        desvio_padrao([1])
'''

README = '''# projeto-fixture

Projeto criado para o teste de aceitacao do Sprint DEVFACTORY-001.

## Estado inicial (de proposito)
- `src/stats.py` tem `mediana()` e `desvio_padrao()` implementadas de forma ERRADA.
- `tests/test_stats.py` prova o comportamento correto -> os testes FALHAM.

## A task de aceitacao
Corrigir `mediana()` (mediana de verdade, lista ordenada, sem mutar a entrada) e
`desvio_padrao()` (denominador amostral N-1), sem quebrar `amplitude()`.

## Rodar
    python3 -m pytest tests -q
'''

PYPROJECT = '''[project]
name = "projeto-fixture"
version = "0.1.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
'''


def criar(destino: Path = DESTINO_PADRAO) -> Path:
    if destino.exists():
        shutil.rmtree(destino)
    (destino / "src").mkdir(parents=True)
    (destino / "tests").mkdir(parents=True)
    (destino / "src" / "stats.py").write_text(STATS, encoding="utf-8")
    (destino / "tests" / "test_stats.py").write_text(TESTES, encoding="utf-8")
    (destino / "README.md").write_text(README, encoding="utf-8")
    (destino / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")

    def git(*a):
        subprocess.run(["git", *a], cwd=str(destino), check=True,
                       capture_output=True)

    git("init", "-q")
    git("config", "user.email", "fixture@localhost")
    git("config", "user.name", "fixture")
    git("add", "-A")
    git("commit", "-q", "-m", "estado inicial: mediana e desvio_padrao quebrados")
    return destino


if __name__ == "__main__":
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO_PADRAO
    p = criar(d)
    print(f"fixture criado em {p}")
    r = subprocess.run(["python3", "-m", "pytest", "tests", "-q"], cwd=str(p),
                       capture_output=True, text=True)
    print(f"estado inicial dos testes (esperado: FALHAM): exit={r.returncode}")
    print((r.stdout or r.stderr).strip()[-600:])
