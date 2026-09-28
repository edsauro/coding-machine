"""Diz se há uma rodada do orquestrador em andamento. Só leitura.

Por que não usar `pgrep -f "autodev ... run"`: o padrão casa com o PRÓPRIO wrapper de
quem pergunta (o texto do comando contém o padrão), e nesta sessão isso já produziu
falso positivo — um guarda assim responderia "já há rodada em andamento" para sempre
e a sprint nunca retomaria.

Fonte da verdade: a tabela `writer_lock` do motor, uma linha por worktree em uso. O
`pid` gravado nem sempre é útil (o motor grava 0 em alguns caminhos), então o que
conta é o **heartbeat recente** — uma linha com heartbeat antigo é resíduo de um
processo interrompido, não uma rodada viva.

Uso:
    python3 .autodev/scripts/rodada_em_andamento.py          # imprime sim/nao
    python3 .autodev/scripts/rodada_em_andamento.py --quiet  # só o código de saída

Saída: 0 = rodada em andamento, 1 = nenhuma rodada.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DB = RAIZ / ".autodev" / "state.db"
JANELA_PADRAO = 900          # 15 min sem heartbeat = resíduo, não rodada viva


def _pid_vivo(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)      # sinal 0 = só verifica existência
        return True
    except (OSError, ValueError):
        return False


def rodadas_em_andamento(janela: int = JANELA_PADRAO,
                         agora: float | None = None) -> list[tuple[str, str, int]]:
    """[(worktree, task_id, pid)] das linhas que representam rodada viva.

    Fontes, em ordem de confiança:
      1. `run_lock` — a fonte da verdade desde a D-24: o motor só escreve ali a
         rodada dona do sprint, e mantém o heartbeat renovado a cada 20 s, inclusive
         durante as revisões longas. Linha com processo VIVO = rodada viva, ponto.
      2. `writer_lock` + heartbeat — rede de segurança para bases gravadas por
         versões anteriores do motor.
    """
    if not DB.exists():
        return []
    agora = time.time() if agora is None else agora
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        try:
            run = con.execute(
                "SELECT sprint_id, pid, heartbeat FROM run_lock").fetchall()
        except sqlite3.OperationalError:
            run = []
        linhas = con.execute(
            "SELECT worktree, COALESCE(task_id,''), COALESCE(pid,0), "
            "COALESCE(heartbeat,0) FROM writer_lock").fetchall()
    except sqlite3.OperationalError:
        return []                 # base sem a tabela: nada rodando
    finally:
        con.close()
    vivas = []
    for sprint_id, pid, hb in run:
        recente = (agora - float(hb or 0)) < janela
        if _pid_vivo(pid) or recente:
            vivas.append((f"run_lock:{sprint_id}", "(sprint)", int(pid or 0)))
    for w, t, p, hb in linhas:
        recente = (agora - float(hb)) < janela
        if _pid_vivo(p) or recente:
            vivas.append((w, t, int(p)))
    return vivas


def main() -> int:
    ativas = rodadas_em_andamento()
    if "--quiet" not in sys.argv:
        if ativas:
            for w, t, p in ativas:
                pid = f" pid={p}" if p else ""
                print(f"sim {t or '?'}{pid} ({Path(w).name})")
        else:
            print("nao")
    return 0 if ativas else 1


if __name__ == "__main__":
    raise SystemExit(main())
