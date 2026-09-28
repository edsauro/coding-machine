#!/usr/bin/env python3
"""Preenche `attempts.tokens_total` a partir do que sobrou em disco (P-10).

ATENÇÃO — o passado quase não é recuperável, e isso é um fato, não uma limitação
de esforço: o wrapper `ask-codex` mandava toda a saída crua do CLI (onde vive o
rodapé `tokens used`) para um `mktemp` apagado no `trap` de saída. Só escaparam as
chamadas em que o wrapper caiu no fallback `tail -20` do stderr.

Fontes, na ordem em que este script tenta:
  1. `<log>.uso`      sidecar do wrapper (existe a partir de 28/09/2026, P-10);
  2. `<log>.uso.bruto` as últimas 200 KB da saída crua do CLI;
  3. o próprio log do motor — só acha quando o rodapé vazou para stdout/stderr.

Uso:
    .venv/bin/python .autodev/scripts/backfill_tokens.py [--gravar]
Sem --gravar só relata o que seria preenchido (dry-run é o default).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from autodev.agents import ler_uso, parse_tokens_do_texto  # noqa: E402
from autodev.state import StateStore  # noqa: E402


def candidatos(log_path: str) -> list[tuple[str, str]]:
    """(caminho, descrição) das fontes que existem para este log."""
    p = Path(log_path) if log_path else None
    if not p:
        return []
    out = []
    for sufixo, desc in ((".uso", "sidecar do wrapper"),
                         (".uso.bruto", "saida crua do CLI"),
                         ("", "log do motor")):
        f = Path(str(p) + sufixo)
        if f.exists():
            out.append((str(f), desc))
    return out


def tokens_de(caminho: str) -> tuple[int | None, str]:
    if caminho.endswith(".uso"):
        return ler_uso(Path(caminho))
    try:
        return parse_tokens_do_texto(Path(caminho).read_text(encoding="utf-8",
                                                             errors="replace")), ""
    except OSError:
        return None, ""


def main(gravar: bool) -> int:
    store = StateStore(RAIZ / ".autodev" / "state.db")
    linhas = list(store.conn.execute(
        "SELECT sprint_id, task_id, attempt, agent, log_path, tokens_total"
        " FROM attempts WHERE tokens_total IS NULL ORDER BY sprint_id, task_id, attempt"))
    achados = []
    for r in linhas:
        for caminho, desc in candidatos(r["log_path"]):
            total, fonte = tokens_de(caminho)
            if total is not None:
                achados.append((r, total, fonte or desc))
                break
    com_fonte = sum(1 for r in linhas if candidatos(r["log_path"]))
    print(f"tentativas sem tokens gravados: {len(linhas)}")
    print(f"  com alguma fonte em disco:     {com_fonte}")
    print(f"  sem fonte nenhuma:             {len(linhas) - com_fonte}")
    print(f"  número recuperável:            {len(achados)}")
    for r, total, fonte in achados:
        print(f"    {r['sprint_id'][-3:]}/{r['task_id']} {r['attempt']}ª "
              f"{r['agent']}: {total:,} tokens ({fonte})")
        if gravar:
            store.gravar_tokens(r["sprint_id"], r["task_id"], r["attempt"],
                                total, f"backfill:{fonte}")
    if achados and not gravar:
        print("\n(dry-run — rode com --gravar para escrever)")
    # cobertura do lado dos dados: quantas tentativas têm tokens hoje
    tot = store.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    ok = store.conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE tokens_total IS NOT NULL").fetchone()[0]
    print(f"\ncobertura atual: {ok}/{tot} tentativas com tokens "
          f"({100.0 * ok / max(1, tot):.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main("--gravar" in sys.argv))
