"""Re-revisão LLM da P09 e da P10 — as duas que só tiveram o portão determinístico.

Em 28/09 09:52 e 09:54, agy e Hermes estavam sem cota, e o portão determinístico
aprovou as duas tasks finais (a cadeia de reserva do autor permite: o revisor nunca
para o sprint). Com créditos repostos, o autor pediu continuidade — aqui as duas
revisões são refeitas por LLM de verdade, sobre o MESMO diff que foi integrado.

Somente leitura: usa os worktrees das tasks no HEAD já integrado, não escreve no
estado do sprint e não commita nada. O veredito novo é evidência, não estado.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

RAIZ = Path("/home/saurus/Code/Coding_Machine")
DB = RAIZ / ".autodev" / "state.db"
LOGS = Path("/home/saurus/.hermes/cache/scratch/revisoes-p09-p10")
sys.path.insert(0, str(RAIZ))

from autodev import review                      # noqa: E402
from autodev.config import Config, carrega_dag  # noqa: E402

SPRINT = "DEVFACTORY-002"
EXEMPLARES = [
    ("P09", RAIZ / ".autodev/worktrees/DEVFACTORY-002-P09-agy", "deepseek-flash"),
    ("P10", RAIZ / ".autodev/worktrees/DEVFACTORY-002-P10-codex", "deepseek-flash"),
]


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True).stdout.strip()


def tentativa_registrada(con: sqlite3.Connection, task: str) -> dict:
    linha = con.execute(
        "SELECT attempt, model, effort, test_result FROM attempts "
        "WHERE sprint_id=? AND task_id=? ORDER BY attempt DESC LIMIT 1",
        (SPRINT, task)).fetchone()
    if not linha:
        return {}
    return {"attempt": linha[0], "model": linha[1], "effort": linha[2],
            "testes": json.loads(linha[3]) if linha[3] else {}}


def main() -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    cfg = Config.carregar()
    dag = carrega_dag(RAIZ / ".autodev" / "sprints" / SPRINT / "dag.json")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    for task_id, worktree, modelo in EXEMPLARES:
        if not worktree.exists():
            print(f"{task_id}: worktree ausente ({worktree})")
            continue
        head = git("rev-parse", "HEAD", cwd=worktree)
        base = git("rev-parse", "HEAD^", cwd=worktree)
        tarefa = next(t for t in dag["tasks"] if t["id"] == task_id)
        reg = tentativa_registrada(con, task_id)
        testes = reg.get("testes") or {}
        texto_testes = (f"{testes.get('passed')} passed, {testes.get('failed')} failed "
                        f"({testes.get('comando', '')})")

        print(f"\n===== {task_id}: {tarefa['titulo']}")
        print(f"  diff {base[:8]}..{head[:8]} · implementado por codex "
              f"{reg.get('model')} {reg.get('effort') or ''} (tentativa {reg.get('attempt')})")
        print(f"  revisor: hermes {modelo} (portão determinístico na rodada original)")

        prompt = review.PROMPT.format(
            criterios="\n".join(f"- {c}" for c in tarefa["criterios"]),
            task_id=task_id, titulo=tarefa["titulo"], base=base,
            diff=review._diff(str(worktree), base), testes=texto_testes,
            arquitetura="simplicidade > confiabilidade > recuperacao > "
                        "verificacao deterministica > seguranca",
            seguranca="sem sudo/root, sem credenciais, sem escrita fora do repo")
        r, motivo = review._revisar_por_llm(
            "hermes", modelo, prompt + review.PROMPT_SO_LEITURA, str(worktree),
            cfg, str(LOGS), f"{task_id}-relido")
        if r is None:
            print(f"  FALHOU: {motivo}")
            continue
        r = review._aplica_piso(r, str(worktree), base, tarefa["criterios"], texto_testes)
        print(f"  veredito: {r.veredito}  (confianca {r.confianca})")
        print(f"  resumo: {r.resumo[:400]}")
        for f in r.findings:
            print(f"   [{f.get('severidade')}] {f.get('arquivo')}:"
                  f"{f.get('linha')} {(f.get('descricao') or '')[:220]}")
        (LOGS / f"{task_id}-veredito-relido.json").write_text(
            json.dumps({"task_id": task_id, "head": head, "base": base,
                        "veredito": r.veredito, "confianca": r.confianca,
                        "resumo": r.resumo, "findings": r.findings},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    con.close()
    print(f"\nvereditos salvos em {LOGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
