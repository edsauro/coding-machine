"""Mede a prova de cobertura da P09 (base a9445a3 vs HEAD 1942b63), de verdade.

A re-revisão LLM da P09 (28/09, com créditos repostos) reprovou com um achado ALTA:
o `decisions.md` da branch admite que a contagem de cobertura exigida pelo critério
D-19 "nao pode ser comprovada" — e justifica com um diagnóstico de ambiente errado
(o python3 do sistema não tem pytest; o correto é o interpretador do projeto,
`.venv/bin/python`, como diz o próprio README).

Aqui a contagem é medida, não declarada: dois worktrees temporários (base e HEAD),
mesmo interpretador do projeto, `pytest --collect-only -q` nos dois. Somente leitura
sobre o repositório; os worktrees temporários são removidos no fim.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path("/home/saurus/Code/Coding_Machine")
PY = RAIZ / ".venv" / "bin" / "python"
RASCUNHO = Path("/home/saurus/.hermes/cache/scratch")
BASE = "a9445a39405ba7cbef87a8c103ad9a2ed9104268"
HEAD = "1942b63e"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=RAIZ, capture_output=True,
                          text=True).stdout.strip()


def conta(commit: str) -> tuple[int, int, str]:
    """(coletados, definicoes de teste, tail do output) para um commit."""
    destino = RASCUNHO / f"wt-p09-{commit[:7]}"
    shutil.rmtree(destino, ignore_errors=True)
    subprocess.run(["git", "worktree", "remove", "--force", str(destino)],
                   cwd=RAIZ, capture_output=True)
    add = subprocess.run(["git", "worktree", "add", "--detach", str(destino), commit],
                         cwd=RAIZ, capture_output=True, text=True)
    if add.returncode != 0:
        return -1, -1, f"falhou o worktree: {add.stderr.strip()[:200]}"
    try:
        saida = subprocess.run(
            [str(PY), "-m", "pytest", ".autodev/tests/", "--collect-only", "-q",
             "-p", "no:cacheprovider"],
            cwd=destino, capture_output=True, text=True, timeout=600).stdout
        m = re.search(r"(\d+)\s+tests? collected", saida)
        coletados = int(m.group(1)) if m else -1
        defs = 0
        for arq in (destino / ".autodev" / "tests").glob("test_*.py"):
            defs += len(re.findall(r"^def test_", arq.read_text(encoding="utf-8"), re.M))
        return coletados, defs, saida.strip().splitlines()[-1] if saida.strip() else ""
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(destino)],
                       cwd=RAIZ, capture_output=True)
        shutil.rmtree(destino, ignore_errors=True)


def main() -> int:
    print("prova de cobertura da P09 — interpretador do projeto:", PY)
    print()
    resultados = {}
    for nome, commit in (("base (antes da P09)", BASE), ("HEAD integrado (P09)", HEAD)):
        coletados, defs, tail = conta(commit)
        resultados[nome] = (coletados, defs)
        print(f"{nome}: {commit[:8]}")
        print(f"  coletados: {coletados}   defs de teste: {defs}")
        print(f"  ultima linha: {tail}")
        print()
    (cb, db), (ch, dh) = resultados["base (antes da P09)"], resultados["HEAD integrado (P09)"]
    if cb < 0 or ch < 0:
        print("MEDICAO INCOMPLETA — nao da para afirmar nada")
        return 1
    veredito = "COBERTURA NAO CAIU" if ch >= cb else "COBERTURA CAIU"
    print(f"{veredito}: coletados {cb} -> {ch} ({ch - cb:+d}) · "
          f"defs {db} -> {dh} ({dh - db:+d})")
    return 0 if ch >= cb else 1


if __name__ == "__main__":
    raise SystemExit(main())
