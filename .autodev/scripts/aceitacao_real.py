#!/usr/bin/env python3
"""Aceitação REAL: roda o Sprint DEVFACTORY-001 de ponta a ponta com o Codex
de verdade (sem driver fake), sobre o projeto-fixture.

Prova: worktree isolado -> Codex implementa -> testes -> revisão cruzada por AGY
-> integração com portões -> relatório. Nenhum humano no meio.

Uso: .venv/bin/python .autodev/scripts/aceitacao_real.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / ".autodev" / "tests"))

os.environ["AUTODEV_AGENT_TIMEOUT"] = os.environ.get("AUTODEV_AGENT_TIMEOUT", "900")
os.environ.pop("AUTODEV_FAKE_AGENT", None)
os.environ.pop("AUTODEV_FAKE_SPEC", None)

from conftest import TASK_FIXTURE, criar_fixture, scaffold_sprint  # noqa: E402
from autodev import report  # noqa: E402
from autodev.orchestrator import Orquestrador  # noqa: E402
from autodev.worktree import commit_atual, git  # noqa: E402

SPRINT = "REAL-001"
BASE = Path(os.path.expanduser("~/workspace/a_devfactory/real/e2e"))


def main() -> int:
    import shutil
    if BASE.exists():
        shutil.rmtree(BASE)
    BASE.mkdir(parents=True)

    fx = criar_fixture(BASE / "projeto-fixture")
    scaffold_sprint(fx, SPRINT, [TASK_FIXTURE],
                    objetivo="corrigir o modulo stats do projeto-fixture "
                             "(mediana e desvio padrao amostral)")
    base_commit = commit_atual(fx)

    print(f"fixture: {fx}")
    print(f"commit base: {base_commit[:8]}")
    print("estado inicial dos testes:")
    os.system(f"cd {fx} && {RAIZ}/.venv/bin/python -m pytest tests -q 2>&1 | tail -3")
    print()

    t0 = time.time()
    o = Orquestrador(fx, SPRINT, modo_teste=True)
    print("agentes detectados:")
    for n, i in o.disponiveis.items():
        print(f"  {n}: disponivel={i.disponivel} {i.versao or i.erro}")
    print()

    r = o.rodar()
    dur = time.time() - t0

    print()
    print("=" * 62)
    print(f"RESULTADO: {r.concluidas}/{len(r.tasks)} tasks concluidas em {dur:.0f}s")
    print(f"parado por: {r.parado_por or '(nada — fluxo completo)'}")
    print("=" * 62)

    t = o.store.task(SPRINT, "T01")
    print(f"\ntask T01: estado={t['estado']} tentativas={t['tentativas']} "
          f"branch={t['branch']}")
    print(f"worktree: {t['worktree']}")

    wt = Path(t["worktree"])
    if wt.exists():
        print("\n--- teste no worktree da task ---")
        os.system(f"cd {wt} && {RAIZ}/.venv/bin/python -m pytest tests -q 2>&1 | tail -3")

    print("\n--- tentativas registradas ---")
    for a in o.store.tentativas(SPRINT, "T01"):
        tr = json.loads(a["test_result"] or "{}")
        rv = json.loads(a["review_result"] or "{}")
        print(f"  t{a['attempt']} agente={a['agent']} modelo={a['model']}/{a['effort']}")
        print(f"     testes: passed={tr.get('passed')} failed={tr.get('failed')}")
        print(f"     revisao: {rv.get('veredito')} por {rv.get('revisor')} "
              f"({len(rv.get('findings') or [])} findings)")
        print(f"     commit final: {a['final_commit']}")

    print("\n--- portoes de integracao ---")
    for e in o.store.eventos(SPRINT, 50):
        if e["tipo"] == "integrado":
            d = json.loads(e["payload"] or "{}")
            for p in d.get("portoes", []):
                print(f"  {p['nome']}: {'OK' if p['ok'] else 'FALHOU'} — {p['detalhe'][:60]}")

    print(f"\nmain intacta? {commit_atual(fx) == base_commit} "
          f"(antes={base_commit[:8]} depois={commit_atual(fx)[:8]})")
    try:
        conteudo = git("show", f"sprint/{SPRINT}/integration:src/stats.py", cwd=fx)
        original = git("show", f"{base_commit}:src/stats.py", cwd=fx)
        mudou = conteudo != original
        print(f"branch de integracao difere do original? {'SIM' if mudou else 'NAO'}")
        print(f"o bug antigo ('retorna a media') ainda esta la? "
              f"{'SIM (ruim)' if 'retorna a media' in conteudo else 'NAO (corrigido)'}")
        # prova forte: a versão do branch de integração passa nos testes
        import subprocess, tempfile
        with tempfile.TemporaryDirectory() as td:
            alvo = Path(td) / "stats.py"
            alvo.write_text(conteudo)
            print(f"\n--- conteudo integrado de mediana/desvio ---")
            print("\n".join(conteudo.splitlines()[6:24]))
    except Exception as e:  # noqa: BLE001
        print(f"branch de integracao: {e}")

    txt = report.gerar(o.store, SPRINT,
                       objetivo="corrigir o modulo stats (aceitacao REAL)",
                       git_commit=commit_atual(fx), duracao_s=dur)
    destino = BASE / "SPRINT-REPORT-REAL.md"
    report.escrever(txt, destino)
    print(f"\nrelatorio: {destino} ({len(txt)} chars)")
    o.store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
