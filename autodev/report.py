"""DEVFACTORY — geração do relatório do Sprint (T13, spec §29).

Lê o estado persistido (SQLite + arquivos do sprint) e produz o relatório com as
16 seções exigidas. O relatório é derivado do estado — não de memória
conversacional (spec §7).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path


def _fmt_ts(ts) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


def _tabela(cabecalhos: list[str], linhas: list[list]) -> str:
    if not linhas:
        return "_nenhum_\n"
    out = ["| " + " | ".join(cabecalhos) + " |",
           "|" + "|".join("---" for _ in cabecalhos) + "|"]
    for l in linhas:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in l) + " |")
    return "\n".join(out) + "\n"


def gerar(store, sprint: str, *, objetivo: str = "", aceitacao: dict | None = None,
          devios: list[str] | None = None, divida: list[str] | None = None,
          quota_teste: dict | None = None, git_commit: str = "",
          duracao_s: float = 0.0) -> str:
    m = store.metricas(sprint)
    tasks = store.tasks(sprint)
    haq = store.haq_listar(sprint)
    eventos = store.eventos(sprint, limite=2000)
    aceitacao = aceitacao or {}

    # --- 3/4. tasks -----------------------------------------------------------
    concluidas = [t for t in tasks if t["estado"] in ("DONE", "INTEGRATED")]
    bloqueadas = [t for t in tasks if t["estado"] == "BLOCKED"]
    outros = [t for t in tasks if t not in concluidas and t not in bloqueadas]

    tabela_tasks = _tabela(
        ["task", "estado", "agente", "tentativas", "esperas_cota", "tier"],
        [[t["task_id"], t["estado"], t["agente"] or "-", t["tentativas"],
          t["esperas_cota"], t["tier_atual"]] for t in tasks])

    # --- 11. retries/falhas ---------------------------------------------------
    tent = [dict(r) for r in
            store.conn.execute("SELECT * FROM attempts WHERE sprint_id=?"
                               " ORDER BY task_id, attempt", (sprint,)).fetchall()]
    tabela_tent = _tabela(
        ["task", "#", "agente", "modelo", "effort", "status", "exit",
         "failure_class", "duracao_s"],
        [[t["task_id"], t["attempt"], t["agent"], t["model"] or "-",
          t["effort"] or "-", t["status"] or "-", t["exit_code"],
          t["failure_class"] or "-",
          round((t["end_time"] or 0) - (t["start_time"] or 0), 1)]
         for t in tent])

    escalonamentos = [t for t in tent
                      if t["attempt"] and t["attempt"] > 1 and t["model"]
                      and t["failure_class"]]
    modelos_usados: dict[str, int] = {}
    for t in tent:
        k = f"{t['agent']}/{t['model'] or '?'}/{t['effort'] or '?'}"
        modelos_usados[k] = modelos_usados.get(k, 0) + 1

    esperas = store.conn.execute(
        "SELECT * FROM resource_waits WHERE sprint_id=? ORDER BY detectado_em",
        (sprint,)).fetchall()

    # --- 5. evidência de teste ------------------------------------------------
    resultados_teste = []
    for t in tent:
        if t["test_result"]:
            try:
                resultados_teste.append((t["task_id"], t["attempt"],
                                         json.loads(t["test_result"])))
            except json.JSONDecodeError:
                pass
    tabela_testes = _tabela(
        ["task", "#", "comando", "exit", "passed", "failed", "passou"],
        [[a, b, r.get("comando", "-"), r.get("exit_code"), r.get("passed"),
          r.get("failed"), r.get("passou")] for a, b, r in resultados_teste])

    # --- 7. findings de revisão ----------------------------------------------
    findings = []
    for t in tent:
        if t["review_result"]:
            try:
                rv = json.loads(t["review_result"])
            except json.JSONDecodeError:
                continue
            for f in rv.get("findings", []):
                findings.append([t["task_id"], rv.get("revisor", "-"),
                                 f.get("severidade", "-"), f.get("arquivo", "-"),
                                 str(f.get("descricao", ""))[:90]])
    tabela_findings = _tabela(["task", "revisor", "severidade", "arquivo", "descricao"],
                              findings)

    # --- 13. HAQ --------------------------------------------------------------
    tabela_haq = _tabela(["HAQ", "task", "reason", "risk", "status"],
                         [[h["haq_id"], h["task_id"] or "-",
                           str(h["reason"]).split("\n")[0][:80], h["risk"],
                           h["status"]] for h in haq])

    # --- origem das conclusões ------------------------------------------------
    # Nem toda task concluída foi executada pelo laço do orquestrador. Sem esta
    # coluna, o relatório deixaria o leitor concluir que as 15 tasks saíram do
    # laço autônomo — que é exatamente a leitura errada (decisions.md D-13).
    origens: dict[str, str] = {}
    for r in store.conn.execute(
            "SELECT task_id, origem FROM attempts WHERE sprint_id=? ORDER BY attempt",
            (sprint,)):
        origens[r["task_id"]] = r["origem"] or "orquestrador"
    contagem_origem: dict[str, int] = {}
    for o in origens.values():
        contagem_origem[o] = contagem_origem.get(o, 0) + 1
    resumo_origem = ", ".join(f"**{n}** {o}" for o, n in sorted(contagem_origem.items()))
    if any(o != "orquestrador" for o in origens.values()):
        nota_origem = (
            "\n> **Atenção:** `origem` distingue execução do orquestrador de "
            "conclusão por evidência. `retroativo` = o trabalho foi feito fora do "
            "laço e registrado a partir do artefato verificável; `aceitacao_real` = "
            "execução autônoma de verdade. Ver `decisions.md` D-13.")
    else:
        nota_origem = ""

    # --- 9. cota --------------------------------------------------------------
    linhas_cota = [[_fmt_ts(e["detectado_em"]), e["task_id"], e["agent"],
                    _fmt_ts(e["retry_after"]),
                    round((e["retry_after"] - e["detectado_em"]) / 60, 1),
                    "sim" if e["resolvido"] else "nao"] for e in esperas]
    tabela_cota = _tabela(["detectado", "task", "agente", "retomar em",
                           "espera_min", "resolvido"], linhas_cota)

    # --- 14/15 ----------------------------------------------------------------
    har = None
    if m["done"]:
        har = round(len(haq) / m["done"], 3)

    # --- aceitação ------------------------------------------------------------
    tabela_aceitacao = _tabela(
        ["criterio", "ok", "evidencia"],
        [[k, "OK" if v.get("ok") else "FALHOU", str(v.get("evidencia", ""))[:110]]
         for k, v in aceitacao.items()])

    # --- ciclo de vida do sprint ---------------------------------------------
    estado_spr = store.estado_sprint(sprint) or "PLANEJADO"
    caminho_spr = " → ".join(
        h["estado"] for h in reversed(store.historico_checkpoints(sprint))) or "-"

    cab = f"""# SPRINT {sprint} — RELATÓRIO FINAL

**Objetivo:** {objetivo}
**Gerado em:** {_fmt_ts(time.time())}
**Duração total:** {round(duracao_s / 60, 1)} min
**Commit do repositório:** `{git_commit or '-'}`
**Estado do sprint:** `{estado_spr}` — percurso: {caminho_spr}
**Fonte da verdade:** `state.db` + arquivos do Sprint + Git + evidências de teste

---
"""

    s1 = f"""
## 1. Resultado executivo

- Tasks no Sprint: **{m['tasks_total']}** — concluídas **{m['done']}**, bloqueadas
  **{m['blocked']}**, falhadas **{m['failed']}**, aguardando recurso **{m['waiting_resource']}**
- Origem das conclusões: {resumo_origem or 'n/d'}
- Tentativas registradas: **{m['tentativas_total']}** (executadas pelo orquestrador: **{m['tentativas_orquestrador']}**) — as demais são conclusões por evidência, registradas para auditoria
- Esperas de cota do Codex: **{m['esperas_cota']}**
- Itens de HAQ: **{m['haq']}** (abertos: **{m['haq_abertos']}**)
- HAR (itens de HAQ / tasks úteis entregues): **{har if har is not None else 'n/d'}**
"""

    s2 = """
## 2. Arquitetura implementada

Loop autônomo: `SPEC -> PLAN -> DAG -> WORKTREE -> AGENTE -> TESTE -> REVISÃO ->
FIX -> RETESTE -> INTEGRAÇÃO -> VALIDAÇÃO -> RELATÓRIO`

Módulos em `autodev/`:

| módulo | papel | spec |
|---|---|---|
| `state.py` | store SQLite: tasks, tentativas, eventos, HAQ, kill switches, checkpoint | §4 §7 §22 |
| `config.py` | schemas + validação do DAG e da máquina de estados | §4 §5 |
| `errors.py` | taxonomia de falhas e classificação | §20 |
| `agents.py` | detecção + adaptadores Codex/AGY/Hermes + driver determinístico | §8 §9 |
| `worktree.py` | gerência de worktrees e branches por task | §15 |
| `sandbox.py` | isolamento via bubblewrap (sem daemon, sem root) | §7 §18 |
| `testrunner.py` | testes determinísticos + assinatura de falha (anti-loop) | §17 |
| `review.py` | revisor cruzado + portões objetivos de segurança | §16 |
| `retry.py` | fingerprint, escalonamento, continuação pós-cota | §10 §11 §12 §13 |
| `haq.py` | fila de ação humana, com comando exato e verificação | §19 |
| `integration.py` | branch de integração + portões de merge | §23 |
| `report.py` | este relatório | §29 |
| `killswitch.py` | paradas hierárquicas STOP_ALL/PROJECT/SPRINT/TASK | §26 |
| `orchestrator.py` | o laço de longo horizonte e a recuperação de crash | §7 §21 |
"""

    s3 = f"""
## 3. Tasks concluídas

{_tabela(['task', 'estado', 'agente', 'origem', 'tentativas', 'esperas_cota', 'tier'],
         [[t['task_id'], t['estado'], t['agente'] or '-',
           origens.get(t['task_id'], '-'), t['tentativas'],
           t['esperas_cota'], t['tier_atual']]
          for t in concluidas])}
{nota_origem}
_(estado de todas as tasks abaixo)_

{tabela_tasks}
## 4. Tasks bloqueadas

{_tabela(['task', 'motivo'],
         [[t['task_id'], str(t['bloqueio'] or '')[:120]] for t in bloqueadas])}
Tasks em andamento / não iniciadas: **{len(outros)}**
"""

    s5 = f"""
## 5. Evidência de teste

{tabela_testes}
## 6. Evidência do teste de aceitação

{tabela_aceitacao}
"""
    if quota_teste:
        s5 += f"""
**Teste de exaustão de cota simulada:**

- cota detectada em: {quota_teste.get('detectado_em', '-')}
- estado persistido: `{quota_teste.get('estado', '-')}`
- retomada agendada para: {quota_teste.get('retry_after', '-')}
- estado restaurado: {quota_teste.get('restaurado', '-')}
- prompt de continuação continha "NAO RECOMECE": {quota_teste.get('continuacao_explicita', '-')}
- task continuou (não reiniciou): {quota_teste.get('continuou', '-')}
"""

    s7 = f"""
## 7. Findings de revisão de código

{tabela_findings}
## 8. Findings de segurança

Portão de segurança executado na integração (segredos versionados, chaves
privadas, escrita em caminho absoluto do HOME, `shell=True`).
Isolamento de sandbox verificado por execução real de `test -r` dentro do bwrap.

## 9. Cota do Codex e recuperação

{tabela_cota}
## 10. Uso de modelos e escalonamentos

{_tabela(['agente/modelo/effort', 'chamadas'],
         [[k, v] for k, v in sorted(modelos_usados.items(), key=lambda x: -x[1])])}
Tentativas acima da primeira (candidatas a escalonamento): **{len(escalonamentos)}**

## 11. Retries e falhas

{tabela_tent}
## 12. HAQ (Human Action Queue)

{tabela_haq}
"""

    s13 = f"""
## 13. Desvios da arquitetura original

{_tabela(['desvio'], [[d] for d in (devios or [])])}
## 14. Dívida técnica

{_tabela(['item'], [[d] for d in (divida or [])])}
## 15. Impacto esperado no HAR

HAR = Human Attention Required / Useful Work Delivered.
Neste Sprint: **{len(haq)}** item(ns) de HAQ para **{m['done']}** task(s) entregue(s)
-> HAR = **{har if har is not None else 'n/d'}**.

## 16. Recomendação para o Sprint 2

_(ver decisões e recomendações no fim deste documento)_

---
"""

    eventos_resumo = _tabela(
        ["quando", "tipo", "task", "payload"],
        [[_fmt_ts(e["ts"]), e["tipo"], e["task_id"] or "-",
          str(e["payload"])[:90]] for e in eventos[:40]])

    return (cab + s1 + s2 + s3 + s5 + s7 + s13 +
            "\n## Apêndice — últimos eventos do Sprint\n\n" + eventos_resumo)


def escrever(texto: str, caminho: str | Path) -> Path:
    p = Path(caminho)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto, encoding="utf-8")
    return p
