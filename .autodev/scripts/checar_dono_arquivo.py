#!/usr/bin/env python3
"""Check de DONO DE ARQUIVO sobre o DIFF REAL (o item (a)).

O furo: `verificar_plano.py` checa o que o PLANO DECLARA (extrai arquivos do
texto dos criterios). O revisor olha o que o AGENTE FEZ. Entre os dois existe um
buraco: a task promete tocar so os seus arquivos e edita o de outra task em
silencio — foi o que produziu 41 dos 147 findings de REVIEW_FAILURE nas 003/004,
com o verificador dizendo "0 erro, 0 aviso".

Este script fecha o buraco lendo o diff REAL de cada task integrada e cruzando
com os arquivos DECLARADOS pelas outras tasks do mesmo sprint.

Regra (mesmas de A1/A2 do verificador de planos, agora sobre o fato):
  - a task editou arquivo declarado por outra task
  - e NAO depende dela (A2), e nao declarou criterio de preservacao (A1)
  => VIOLACAO

Uso:  python3 checar_dono_arquivo.py <sprint_id> [--json]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path("/home/saurus/Code/Coding_Machine")
sys.path.insert(0, str(RAIZ / ".autodev" / "scripts"))
import verificar_plano as vp  # noqa: E402


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", str(RAIZ), *args],
                       capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def arquivos_reais(sprint: str, task: str) -> list[str]:
    """Arquivos que a task REALMENTE tocou, da PRIMEIRA base ate o commit aprovado.

    Nao usar `merge-base main <branch>`: depois da integracao a main ja contem o
    trabalho da sprint, e o merge-base devolve o proprio tip da task (diff vazio).
    A base de verdade esta gravada no banco, por tentativa.
    """
    import sqlite3
    db = RAIZ / ".autodev" / "state.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    linhas = list(con.execute(
        "SELECT attempt, base_commit, final_commit, changed_files, status"
        " FROM attempts WHERE sprint_id=? AND task_id=? ORDER BY attempt",
        (sprint, task)))
    if not linhas:
        return []
    base = next((l["base_commit"] for l in linhas if l["base_commit"]), None)
    fim = next((l["final_commit"] for l in reversed(linhas)
                if l["final_commit"]), None)
    if base and fim:
        out = git("diff", "--name-only", f"{base}..{fim}")
        if out.strip():
            return [l for l in out.splitlines() if l.strip()]
    # fallback: o proprio registro do motor
    arqs: set[str] = set()
    for l in linhas:
        try:
            arqs.update(json.loads(l["changed_files"] or "[]"))
        except json.JSONDecodeError:
            pass
    return sorted(arqs)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    sprint = sys.argv[1]

    dag = json.loads((RAIZ / ".autodev" / "sprints" / sprint / "dag.json")
                     .read_text(encoding="utf-8"))
    tasks = dag["tasks"]
    por_id = {t["id"]: t for t in tasks}

    declarado = {t["id"]: vp.extrair_tocados(t) | vp.extrair_arquivos(t)
                 for t in tasks}
    deps = {t["id"]: set(t.get("deps", []) or []) for t in tasks}

    # fecha transitivamente as deps: editar arquivo de uma dep indireta e legitimo
    def alcanca(tid: str) -> set[str]:
        visto: set[str] = set()
        pilha = list(deps[tid])
        while pilha:
            d = pilha.pop()
            if d in visto:
                continue
            visto.add(d)
            pilha.extend(deps.get(d, ()))
        return visto

    violacoes = []
    for t in tasks:
        tid = t["id"]
        reais = arquivos_reais(sprint, tid)
        alcancaveis = alcanca(tid)
        for arq in reais:
            for outro, arqs in declarado.items():
                if outro == tid or arq not in arqs:
                    continue
                if outro in alcancaveis:
                    continue          # A2 satisfeita: depende dela
                violacoes.append((tid, arq, outro))

    print("=" * 74)
    print(f"DONO DE ARQUIVO — {sprint} (diff real vs declarado)")
    print("=" * 74)
    if not violacoes:
        print("  nenhuma violacao")
    for tid, arq, dono in violacoes:
        print(f"  VIOLACAO  {tid} editou {arq}")
        print(f"            declarado por {dono}, e {tid} NAO depende de {dono}")
    print(f"\n  total: {len(violacoes)} violacao(oes)")

    if "--json" in sys.argv:
        print(json.dumps([{"task": t, "arquivo": a, "dono": d}
                          for t, a, d in violacoes], ensure_ascii=False))
    return 1 if violacoes else 0


if __name__ == "__main__":
    sys.exit(main())
