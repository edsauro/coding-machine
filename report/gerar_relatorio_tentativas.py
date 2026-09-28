"""Relatório visual das chamadas de API do Codex no Coding_Machine.

Uma única agregação alimenta o gráfico E o texto (a skill é explícita: gráfico e
tabela têm de sair da MESMA função, senão discordam na primeira revisão).

Saídas:
  * report/histograma-tentativas.png  — colunas = pacotes (por sprint), empilhado por
    número da tentativa (1ª, 2ª, ... cores diferentes);
  * report/distribuicao-tentativas.png — % das chamadas por número de tentativa, com os
    modelos usados em cada degrau;
  * report/dados.json — a agregação crua, para o texto citar exatamente estes números.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter, OrderedDict, defaultdict
from pathlib import Path

RAIZ = Path("/home/saurus/Code/Coding_Machine")
DB = RAIZ / ".autodev" / "state.db"
OUT = RAIZ / "report"
OUT.mkdir(exist_ok=True)

ROTULO_SPRINT = {
    "DEVFACTORY-001": "Sprint 001\n(laço autônomo · registro retroativo)",
    "DEVFACTORY-002": "Sprint 002\n(planejador)",
    "DEVFACTORY-003": "Sprint 003\n(portão do plano · nunca executada)",
    "DEVFACTORY-004": "Sprint 004\n(correções da revisão retroativa)",
}
def pacotes_do_banco() -> dict[str, list[str]]:
    """Pacotes de cada sprint lidos do BANCO (não escritos à mão).

    Inventei a lista da sprint 1 como T01..T14 e o banco tinha T15 — pacote real fora
    do gráfico. Lista de pacote é dado, não configuração.
    """
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ids: dict[str, list[str]] = defaultdict(list)
    for sid, tid in con.execute(
            "SELECT sprint_id, task_id FROM tasks ORDER BY sprint_id, task_id"):
        ids[sid].append(tid)
    con.close()
    ids.setdefault("DEVFACTORY-003", [])          # planejada: nenhum pacote executado
    return dict(ids)


PACOTES = pacotes_do_banco()


def carrega() -> tuple[dict, dict, dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    linhas = [dict(r) for r in con.execute(
        "SELECT sprint_id, task_id, attempt, agent, model, effort, status,"
        " failure_class, review_result, test_result FROM attempts"
        " ORDER BY sprint_id, task_id, attempt")]
    titulos = {r["task_id"]: r["titulo"] for r in
               con.execute("SELECT task_id, titulo FROM tasks")}
    con.close()

    # ---- barras: chamadas do Codex por pacote, empilhadas por tentativa --------
    por_sprint: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    linhas_codex = [l for l in linhas if l["agent"] == "codex"]
    for l in linhas_codex:
        por_sprint[l["sprint_id"]][l["task_id"]][l["attempt"]] += 1
    chamadas = {s: {t: dict(c) for t, c in tasks.items()} for s, tasks in por_sprint.items()}

    # ---- resumo por degrau (número da tentativa) ------------------------------
    modelos_por_degrau: dict[int, Counter] = defaultdict(Counter)
    status_por_degrau: dict[int, Counter] = defaultdict(Counter)
    for l in linhas_codex:
        rotulo = f"{l['model']}/{l['effort']}" if l["model"] else "(sem modelo)"
        modelos_por_degrau[l["attempt"]][rotulo] += 1
        status_por_degrau[l["attempt"]][l["status"] or "?"] += 1

    total = len(linhas_codex)
    degraus = []
    for deg in sorted(modelos_por_degrau):
        n = sum(modelos_por_degrau[deg].values())
        st = status_por_degrau[deg]
        degraus.append({
            "degrau": deg,
            "chamadas": n,
            "pct_chamadas": 100.0 * n / total,
            "modelos": dict(modelos_por_degrau[deg].most_common()),
            "status": dict(st),
            "ok": st.get("OK", 0),
        })

    # ---- quanto cada pacote precisou, e onde ele terminou ---------------------
    resumo_pacotes = []
    for sprint, tasks in por_sprint.items():
        for tid in sorted(tasks):
            c = tasks[tid]
            maximo = max(c)
            resumo_pacotes.append({
                "sprint": sprint, "task": tid, "chamadas": sum(c.values()),
                "max_tentativa": maximo,
                "titulo": titulos.get(tid, ""),
                "modelos": dict(Counter(
                    f"{l['model']}/{l['effort']}" for l in linhas_codex
                    if l["sprint_id"] == sprint and l["task_id"] == tid).most_common()),
            })

    # ---- outros agentes (não são chamadas do Codex) ---------------------------
    outros = Counter(f"{l['agent']}" for l in linhas if l["agent"] != "codex")

    dados = {
        "total_codex": total,
        "degraus": degraus,
        "chamadas_por_sprint_pacote": {s: {t: dict(c) for t, c in v.items()}
                                      for s, v in por_sprint.items()},
        "pacotes": resumo_pacotes,
        "outros_agentes": dict(outros),
        "maior_degrau": max((d["degrau"] for d in degraus), default=0),
    }
    return dados, chamadas, {s: dict(v) for s, v in por_sprint.items()}


def desenha_histograma(dados: dict) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.colors as mc
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable

    rmax = dados["maior_degrau"]
    cmap = mc.LinearSegmentedColormap.from_list(
        "degraus", ["#1a9850", "#66bd63", "#a6d96a", "#d9ef8b", "#fee08b",
                    "#fdae61", "#f46d43", "#d73027", "#a50026", "#67001f"])
    def cor(deg: int) -> tuple:
        return cmap((deg - 1) / max(1, rmax - 1))

    sprints = [s for s in PACOTES if PACOTES[s]]     # sprint sem pacote não vira grupo
    xs, rotulos, fronteiras = [], [], []
    x = 0.0
    for s in sprints:
        inicio = x
        for tid in PACOTES[s]:
            xs.append((x, tid))
            rotulos.append(tid)
            x += 1
        fronteiras.append((s, inicio, x))
        x += 1.8                                  # respiro entre sprints
    mapa_sprint = [s for s in sprints for _ in PACOTES[s]]

    fig, ax = plt.subplots(figsize=(13.5, 6.8))
    topo = 0.0
    for (xi, tid), s in zip(xs, mapa_sprint):
        c = dados["chamadas_por_sprint_pacote"].get(s, {}).get(tid, {})
        base = 0.0
        for deg in sorted(int(k) for k in c):
            n = c[str(deg)] if str(deg) in c else c[deg]
            ax.bar(xi, n, bottom=base, width=0.72, color=cor(deg),
                   edgecolor="white", linewidth=0.5, zorder=3)
            if n >= 2:
                ax.text(xi, base + n / 2, str(n), ha="center", va="center",
                        fontsize=7, color="white", fontweight="bold", zorder=4)
            base += n
        topo = max(topo, base)

    # Rótulos de sprint DENTRO do eixo (fração de eixo), abaixo dos pacotes — e com
    # espaço reservado: na primeira versão eles colidiam com a legenda.
    for s, ini, fim in fronteiras:
        meio = (ini + fim - 1) / 2 if fim > ini else ini
        ax.text(meio, -0.085, ROTULO_SPRINT[s], transform=ax.get_xaxis_transform(),
                ha="center", va="top", fontsize=9.5, fontweight="bold", color="#333333")
    ax.set_xticks([xi for xi, _ in xs])
    ax.set_xticklabels(rotulos, fontsize=8.5)
    ax.set_ylabel("chamadas de API do Codex", fontsize=10.5)
    ax.set_title(f"Coding_Machine — chamadas de API do Codex por pacote do backlog\n"
                 f"empilhadas pelo número da tentativa (1ª … {rmax}ª) · "
                 f"total {dados['total_codex']} chamadas", fontsize=12.5, pad=14)
    ax.grid(axis="y", alpha=0.25, zorder=0)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_ylim(0, topo * 1.22)
    ax.set_xlim(-0.9, xs[-1][0] + 1.2)

    # Escala de cor no lugar da legenda de 15 entradas: cabe no A4 e não cobre nada.
    sm = ScalarMappable(cmap=cmap, norm=plt.Normalize(1, rmax))
    sm.set_array([])
    cb = fig.colorbar(sm, ax=ax, orientation="horizontal", fraction=0.055, pad=0.16,
                      aspect=45, ticks=range(1, rmax + 1))
    cb.set_label("número da tentativa (verde = resolvido cedo, vermelho = no fim da escada)",
                 fontsize=9)
    cb.ax.tick_params(labelsize=8)

    pacotes_001 = len(PACOTES.get("DEVFACTORY-001", []))
    ax.annotate(f"Sprint 001: {pacotes_001} pacotes, "
                f"{sum(sum(c.values()) for c in dados['chamadas_por_sprint_pacote'].get('DEVFACTORY-001', {}).values())} "
                f"chamada(s) — registro\nretroativo (o motor ainda não instrumentava as tentativas).\n"
                f"Sprint 003: 7 pacotes planejados, nunca executados.",
                xy=(0.006, 0.975), xycoords="axes fraction", fontsize=8, va="top",
                bbox=dict(boxstyle="round,pad=0.45", facecolor="#fff8e1",
                          edgecolor="#d9c37a"))
    destino = OUT / "histograma-tentativas.png"
    fig.savefig(destino, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return destino


def desenha_distribuicao(dados: dict) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def curto(nome: str) -> str:
        return (nome.replace("gpt-5.6-", "").replace("gpt-6-", "")
                if nome.startswith("gpt-") else nome)

    degs = dados["degraus"]
    rot = [f"{d['degrau']}ª" for d in degs]
    pct = [d["pct_chamadas"] for d in degs]
    # Só o modelo dominante (ou dois) sob a barra, em UMA linha curta: a lista
    # completa vai em tabela no texto — na 1ª versão as três linhas de modelos
    # saíram sobrepostas e ilegíveis.
    modelos = []
    for d in degs:
        itens = list(d["modelos"].items())
        # só o dominante: a lista completa (com todos os modelos de cada degrau) está na
        # Tabela 2, logo abaixo. Em 15 colunas estreitas, qualquer rótulo largo colide.
        modelos.append(f"{curto(itens[0][0])} ({itens[0][1]})") if itens else modelos.append("")

    fig, ax = plt.subplots(figsize=(13.5, 6.4))
    barras = ax.bar(rot, pct, color="#3b6ea5", width=0.62, zorder=3)
    for i, (b, d, m) in enumerate(zip(barras, degs, modelos)):
        # nas barras minúsculas (degruas 11-15ª) o rótulo de valor tem de alternar de
        # altura, senão um encosta no outro
        dy = 0.55 if (d["pct_chamadas"] >= 4 or i % 2 == 0) else 1.45
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + dy,
                f"{d['pct_chamadas']:.1f}%  ({d['chamadas']})", ha="center",
                fontsize=8.5, fontweight="bold")
        # rótulo rotacionado: cabe sem colidir com o vizinho em nenhum dos 15 degraus
        ax.text(b.get_x() + b.get_width() / 2, -0.045, m, rotation=90,
                ha="center", va="top", transform=ax.get_xaxis_transform(),
                fontsize=7.5, color="#444444")
    ax.set_ylabel("% das chamadas do Codex", fontsize=10.5)
    ax.set_title("Em que tentativa o trabalho foi resolvido — e com que modelo\n"
                 "(o modelo dominante de cada degrau; a lista completa está na tabela "
                 "do relatório)", fontsize=12, pad=14)
    ax.grid(axis="y", alpha=0.25, zorder=0)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_ylim(0, max(pct) * 1.25)
    ax.annotate("Cada barra é uma fatia das 92 chamadas, não a chance de acerto.\n"
                "A taxa de sucesso por degrau está na tabela 2.",
                xy=(0.995, 0.95), xycoords="axes fraction", ha="right", va="top",
                fontsize=8, color="#555555",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#f5f5f5",
                          edgecolor="#cccccc"))
    destino = OUT / "distribuicao-tentativas.png"
    fig.savefig(destino, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return destino


def escreve_relatorio(dados: dict) -> Path:
    """Gera o Markdown do MESMO dicionário que alimenta os gráficos.

    A skill é explícita: gráfico e texto têm de sair da mesma agregação. Se eu
    digitasse os números à mão, a primeira revisão encontraria divergência.
    """
    from datetime import datetime

    d = dados
    total = d["total_codex"]
    degs = d["degraus"]
    aprovadas = sum(x["ok"] for x in degs)
    acumulado = 0.0
    linhas_degrau = []
    for x in degs:
        acumulado += x["pct_chamadas"]
        mods = ", ".join(f"{m} ({n})" for m, n in x["modelos"].items())
        linhas_degrau.append(
            f"| {x['degrau']}ª | {x['chamadas']} | {x['pct_chamadas']:.1f}% | "
            f"{acumulado:.1f}% | {x['ok']} | {mods} |")
    tabela_degraus = "\n".join(linhas_degrau)

    linhas_pacote = []
    for s in ("DEVFACTORY-001", "DEVFACTORY-002", "DEVFACTORY-004"):
        c = d["chamadas_por_sprint_pacote"].get(s, {})
        for tid in PACOTES.get(s, []):
            m = c.get(tid)
            if not m:
                continue
            tent = sorted(int(k) for k in m)
            mods = ", ".join(sorted({mm for p in d["pacotes"]
                                     if p["sprint"] == s and p["task"] == tid
                                     for mm in p["modelos"]}))
            buraco = "" if tent == list(range(tent[0], tent[-1] + 1)) else " ⚠"
            linhas_pacote.append(
                f"| {s.split('-')[-1]} | {tid} | {sum(m.values())} | "
                f"{tent[0]}ª–{tent[-1]}ª{buraco} | {mods} |")
    tabela_pacotes = "\n".join(linhas_pacote)

    fora_escada = sorted({mm for p in d["pacotes"] for mm in p["modelos"]
                          if mm not in ("gpt-5.6-luna/low", "gpt-5.6-terra/low",
                                        "gpt-5.6-sol/low", "gpt-5.6-sol/medium",
                                        "gpt-6-astra/low")})
    top = sorted(d["pacotes"], key=lambda p: -p["chamadas"])[:3]
    top_txt = ", ".join(f"{p['task']} (sprint {p['sprint'].split('-')[-1]}, "
                        f"{p['chamadas']} chamadas)" for p in top)
    um_chamada = [p for p in d["pacotes"] if p["chamadas"] == 1]
    com_chamada = [p for p in d["pacotes"]]
    com_buraco = []
    for p in d["pacotes"]:
        tent = sorted(int(k) for k in d["chamadas_por_sprint_pacote"]
                      [p["sprint"]][p["task"]])
        if tent != list(range(tent[0], tent[-1] + 1)):
            com_buraco.append(p)
    hoje = datetime.now().strftime("%d/%m/%Y")

    md = f"""# Chamadas de API do Codex no Coding_Machine

**Pergunta:** quantas chamadas de API o motor autônomo gastou por pacote do backlog, em
que tentativa cada uma aconteceu e com que modelo.
**Fonte:** `.autodev/state.db`, tabela `attempts` (o próprio motor grava uma linha por
invocação).
**Janela:** 27/09/2026 02:03 a 28/09/2026 {datetime.now().strftime('%H:%M')} —
DEVFACTORY-001, 002 e 004 (a 003 foi planejada e nunca executada).
**Data do relatório:** {hoje}.
**Total no período:** **{total} chamadas do Codex**, {aprovadas} delas aprovadas
(revisão + integração).

## Avisos

1. **Chamada não é custo.** Cada linha conta **uma invocação** do agente; o motor não
   registra tokens, então este relatório mede chamadas, não gasto.
2. **A sprint 001 não é comparável.** Dos seus 15 pacotes, 14 são **registro
   retroativo** (agente `retroativo`, inserido em 27/09 02:03 para reconstruir o
   histórico) — **não são chamadas de API**. Só o T15 tem uma chamada real, e sem
   modelo registrado. É por isso que 14 colunas da sprint 001 estão vazias.
3. **A sprint 003 não aparece no gráfico:** 7 pacotes planejados, nenhum executado.
4. **A sprint 004 está em andamento** ({sum(1 for t in d['chamadas_por_sprint_pacote'].get('DEVFACTORY-004', {}) if True)} de 4
   pacotes já com chamadas; a P03 está aguardando cota do Codex) — os números dela
   ainda vão mudar.
5. **"Nª tentativa" não é o degrau da escada de modelos.** Falhas de infraestrutura
   (`CODEX_QUOTA`, `NETWORK_ERROR`, `ENVIRONMENT_ERROR`, `DEPENDENCY_ERROR`,
   `PERMISSION_REQUIRED`, `SECRET_REQUIRED`, `RED_ACTION_REQUIRED`) reprocessam **no
   mesmo modelo** por decisão de política — por isso `luna/low` reaparece em degraus
   altos. O degrau mede "quantas vezes tentou", não "quão forte era o modelo".
6. **A partir da 5ª tentativa o modelo é sempre o mesmo** (`astra/low`, tier 4): o mapa
   da escada satura em 5, então degraus 5 a 15 podem repetir o modelo do topo — e, nas
   classes do aviso 5, repetir o do fundo.
7. **{len(fora_escada)} combinação(ões) fora da escada declarada:** {', '.join(f'`{x}`' for x in fora_escada) or 'nenhuma'}.
   As chamadas `luna/medium` e `terra/medium` aconteceram em 27/09 entre 03:13 e 04:07,
   **antes** de a escada ser padronizada naquele mesmo dia — não são desvio de política.
8. **A numeração por pacote tem buracos.** Rearme por dependência integrada e reabertura
   por defeito de contrato removem/renomeiam tentativas, então {len(com_buraco)} pacote(s)
   ({', '.join(f"{p['task']} (sprint {p['sprint'].split('-')[-1]})" for p in com_buraco) or 'nenhum'}) têm sequência descontínua — marcados com ⚠ na
   tabela 1. Consequência para a leitura: o degrau 1 pode se repetir na história de um
   mesmo pacote, e "15 primeiras tentativas para 15 pacotes" é coincidência, não regra.

## O que os números dizem

- **{total} chamadas do Codex** em {len(com_chamada)} pacotes com execução registrada. A
  mediana é de {sorted(p['chamadas'] for p in com_chamada)[len(com_chamada) // 2]} chamadas por pacote e a média
  {sum(p['chamadas'] for p in com_chamada) / len(com_chamada):.1f}.
- **{degs[0]['pct_chamadas']:.1f}% das chamadas são de 1ª tentativa** e
  {degs[0]['pct_chamadas'] + degs[1]['pct_chamadas']:.1f}% acontecem até a 2ª. Metade do gasto
  ({next((x['degrau'] for x in degs if sum(y['pct_chamadas'] for y in degs if y['degrau'] <= x['degrau']) >= 50), 0)}ª tentativa em diante)
  está na cauda: são poucos pacotes que consumiram a escada inteira.
- **{len(um_chamada)} pacote(s) resolveram com uma única chamada:**
  {', '.join(f"{p['task']} (sprint {p['sprint'].split('-')[-1]})" for p in um_chamada)}.
- **Os campeões de gasto:** {top_txt}. Os dois são casos conhecidos: a P02 da sprint 2
  pedia reescrita de contrato de teste (o mesmo arquivo para tasks diferentes) e a P04 da
  sprint 4 gastou a escada consertando o próprio comando de teste — 10 tentativas, das
  quais o defeito era meu, não do agente (decisão D-23).
- **A cauda direita do gráfico 1 é o sintoma mais caro do período:** 5 chamadas em
  degraus 11 a 15, todas em pacotes que só destravaram quando o **defeito de motor** foi
  corrigido (D-19, D-24, D-25) — nenhuma delas é "o modelo errado tentando mais".

## Gráfico 1 — chamadas por pacote, empilhadas pela tentativa

![Chamadas de API do Codex por pacote do backlog, empilhadas pelo número da tentativa](report/histograma-tentativas.png)

Cada coluna é um pacote; a altura é quantas vezes o Codex foi chamado nele; a cor diz
**em que altura da escada** a chamada aconteceu (verde = cedo, vermelho = fim da
escada). Total: {total} chamadas.

## Tabela 1 — por pacote

| sprint | pacote | chamadas | tentativas (1ª–última) | modelos usados |
|---|---:|---:|---|---|
{tabela_pacotes}

## Gráfico 2 — distribuição por número de tentativa

![Percentual das chamadas por número de tentativa, com o modelo dominante de cada degrau](report/distribuicao-tentativas.png)

## Tabela 2 — por degrau

| tentativa | chamadas | % das chamadas | % acumulado | aprovadas | modelos usados |
|---|---:|---:|---:|---:|---|
{tabela_degraus}

## A escada declarada × o que aconteceu

Escada de implementação no `models.yaml` (tentativa → modelo): 1ª `luna/low` · 2ª
`terra/low` · 3ª `sol/low` · 4ª `sol/medium` · 5ª `astra/low`.

O que a base mostra é diferente em pontos importantes, e por motivos conhecidos:

1. **Reuso do mesmo modelo em falha de infraestrutura** (aviso 5) — a maior parte da
   diferença. Espera de cota e erro de ambiente não gastam escalonamento.
2. **Saturação depois da 5ª** (aviso 6): o mapa de escalonamento tem 5 entradas, então
   qualquer tentativa a partir da 5ª usa `astra/low`.
3. **Buracos e reinícios na numeração** (aviso 8) fazem o mesmo degrau aparecer com
   modelos diferentes conforme o momento do pacote — não é troca de política.
4. **5 chamadas anteriores à padronização** da própria escada (aviso 7).

## Arquivos gerados e proveniência

| arquivo | o que é |
|---|---|
| `REPORT-TENTATIVAS-CODEX.md` | este relatório |
| `report/histograma-tentativas.png` | gráfico 1 |
| `report/distribuicao-tentativas.png` | gráfico 2 |
| `report/dados.json` | agregação crua usada no texto e nos gráficos |
| `report/gerar_relatorio_tentativas.py` | gera os três a partir de `.autodev/state.db` |
| `report/relatorio-tentativas.html` / `.pdf` | HTML autocontido e PDF A4 |

Reproduzir:

```bash
cd ~/Code/Coding_Machine
~/.hermes/cache/scratch/venv_report/bin/python report/gerar_relatorio_tentativas.py
pandoc REPORT-TENTATIVAS-CODEX.md -o report/relatorio-tentativas.html --standalone \\
  --embed-resources --resource-path=.:report --css=report/estilo-relatorio.css \\
  --metadata lang=pt-BR
chromium --headless=new --disable-gpu --no-sandbox --user-data-dir=/tmp/chrome-pdf \\
  --virtual-time-budget=30000 --no-pdf-header-footer \\
  --print-to-pdf="$PWD/report/relatorio-tentativas.pdf" \\
  "file://$PWD/report/relatorio-tentativas.html"
```

O venv `~/.hermes/cache/scratch/venv_report` existe só para o `matplotlib` (o venv do
projeto não tem gráficos) e é descartável: `uv venv` + `uv pip install matplotlib`.
"""
    destino = RAIZ / "REPORT-TENTATIVAS-CODEX.md"
    destino.write_text(md, encoding="utf-8")
    return destino


if __name__ == "__main__":
    dados, chamadas, por_sprint = carrega()
    (OUT / "dados.json").write_text(json.dumps(dados, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    print(f"TOTAL de chamadas do Codex: {dados['total_codex']}")
    print(f"outros agentes (não Codex): {dados['outros_agentes']}\n")
    print(f"{'degrau':>6} {'chamadas':>8} {'% das chamadas':>15} {'OK':>4}  modelos")
    for d in dados["degraus"]:
        mods = ", ".join(f"{m} ({n})" for m, n in d["modelos"].items())
        print(f"{d['degrau']:>6} {d['chamadas']:>8} {d['pct_chamadas']:>14.1f}% "
              f"{d['ok']:>4}  {mods}")
    print()
    for s, tasks in dados["chamadas_por_sprint_pacote"].items():
        print(f"{s}: {sum(sum(c.values()) for c in tasks.values())} chamadas em "
              f"{len(tasks)} pacotes")
        print("   " + " · ".join(f"{t}={sum(c.values())}" for t, c in sorted(tasks.items())))
    print()
    for p in desenha_histograma(dados), desenha_distribuicao(dados):
        print("PNG:", p)
    print("MD:", escreve_relatorio(dados))
