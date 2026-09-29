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
    "DEVFACTORY-003": "Sprint 003\n(em execução desde 28/09)",
    "DEVFACTORY-004": "Sprint 004\n(fechada 4/4 em 28/09)",
}
# ---- atribuição de causa (CURADA, com a decisão que a descreve) --------------
# O motor grava a CLASSE da falha (TEST_FAILURE, REVIEW_FAILURE, CODEX_QUOTA...),
# mas não grava de quem era a culpa. Estas janelas foram atribuídas à mão, olhando o
# log e as decisões — e é por isso que aparecem separadas do dado bruto, nunca no
# lugar dele. Objetivo: medir o modelo sem cobrar dele o defeito do teste/do plano.
CLASSES_INFRA = {"CODEX_QUOTA", "QUOTA_AGENTE", "NETWORK_ERROR", "ENVIRONMENT_ERROR",
                 "DEPENDENCY_ERROR", "PERMISSION_REQUIRED", "SECRET_REQUIRED",
                 "RED_ACTION_REQUIRED", "AGENT_CRASH", "UNKNOWN"}
CAUSA_TESTE_OU_PLANO = {
    ("DEVFACTORY-002", "P02"): ((1, 5), "D-16: duas tasks donas do mesmo arquivo de teste"),
    ("DEVFACTORY-002", "P03"): ((1, 5), "D-16: idem"),
    ("DEVFACTORY-002", "P09"): ((1, 3), "D-16: idem (aprovado e refeito no laço de integração)"),
    ("DEVFACTORY-004", "P01"): ((1, 6), "D-23: critério mandava .venv dentro do worktree"),
    ("DEVFACTORY-004", "P04"): ((1, 5), "D-23: idem"),
}
CAUSA_TESTE_OU_PLANO_EXTRA = {
    ("DEVFACTORY-002", "P09"): ((4, 8), "D-19: contrato de preservação impossível"),
}

# ---- custo: preço do token e a régua de tokens por chamada -------------------
# Preço: assinatura do autor, US$ 20/mês = 4 blocos semanais de 100% → US$ 0,05 por
# ponto da janela SEMANAL; a régua medida no agregado é 118.096 tokens por ponto →
# US$ 0,423/Mtok. (A leitura pela janela de 5h dá US$ 0,37/Mtok — as duas estão no
# relatório de eficiência; aqui vale a semanal, que é a que limita.)
USD_POR_TOKEN = (20.0 / 4 / 100) / 118_096

# Tokens por chamada de modelo que o MOTOR nunca mediu: prévia do A/B de 28/09 (uma
# chamada por braço, no mesmo pacote mínimo). Onde o motor tem medição própria, a média
# dele manda — tarefa real é maior que o pacote da prévia.
TOKENS_DA_PREVIA = {
    "gpt-5.6-luna/low": 24_714, "gpt-5.6-luna/high": 52_239,
    "gpt-5.6-terra/low": 25_069, "gpt-5.6-terra/high": 31_174,
    "gpt-5.6-sol/low": 15_692, "gpt-5.6-sol/high": 17_655,
    "gpt-5.6-sol/medium": 95_364, "gpt-6-astra/low": 47_555,
    "gpt-6-astra/high": 22_094,
}
MEDIDAS: dict = {}       # preenchido em carrega(): as réguas usadas na estimativa

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


def objetivos_por_sprint() -> dict[str, list[tuple[str, str]]]:
    """Objetivo (título) de cada pacote, lido do `dag.json` de cada sprint.

    A fonte é o PLANO que o motor executou, não texto escrito à mão aqui. Pacote sem
    título entra como `(sem título)` em vez de sumir: pacote faltando é dado faltando,
    e dado faltando tem de aparecer.
    """
    out: dict[str, list[tuple[str, str]]] = {}
    for caminho in sorted((RAIZ / ".autodev" / "sprints").glob("*/dag.json")):
        try:
            dag = json.loads(caminho.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pacotes = dag.get("tasks") or dag.get("tarefas") or []
        out[caminho.parent.name] = [
            (str(t.get("id", "?")),
             (t.get("titulo") or "(sem título)").strip().replace("|", "\\|"))
            for t in pacotes]
    return out


# ---- ESCOPO DO RELATÓRIO ------------------------------------------------------
#    Só as sprints do PROTOCOLO ATUAL. A 001 é registro retroativo (14 dos 15 pacotes
#    sem chamada de API nenhuma) e a 002 foi reconstruída depois, com várias
#    aprovações por pacote (até 6) e sem token medido em chamada alguma — juntar as
#    quatro num mesmo gráfico compara protocolos de registro diferentes, não modelos.
#    O filtro acontece AQUI, na fonte, para que texto e gráficos nunca divirjam.
SPRINTS = ("DEVFACTORY-003", "DEVFACTORY-004")
NOTA_PROTOCOLO = (
    "As sprints **001 e 002 estão fora deste relatório**, e o motivo é de registro, não de "
    "mérito: a **001** é história reconstruída (14 dos 15 pacotes são linhas retroativas, "
    "sem chamada de API nenhuma), e a **002** foi levantada depois do fato, com várias "
    "aprovações por pacote (até 6 no mesmo pacote) e sem um único token medido. Nas "
    "sprints **003 e 004** o protocolo é o de hoje: uma chamada de API por tentativa, "
    "revisão registrada e token lido do rodapé do agente. Comparar as quatro juntas "
    "mediria a diferença de protocolo, não a de modelo.")
NOTA_CURTA = "sprints 003 e 004 · 001 e 002 fora (protocolo de registro diferente — ver nota)"


def carrega() -> tuple[dict, dict, dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    linhas = [dict(r) for r in con.execute(
        "SELECT sprint_id, task_id, attempt, agent, model, effort, status,"
        " failure_class, review_result, test_result, tokens_total, start_time FROM attempts"
        " WHERE sprint_id IN ({})"
        " ORDER BY sprint_id, task_id, attempt".format(
            ",".join("?" * len(SPRINTS))), SPRINTS)]
    titulos = {r["task_id"]: r["titulo"] for r in
               con.execute("SELECT task_id, titulo FROM tasks")}
    con.close()

    # ---- barras: chamadas do Codex por pacote, empilhadas por tentativa --------
    por_sprint: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    linhas_codex = [l for l in linhas if l["agent"] == "codex"]

    # ---- réguas de token por chamada (base da estimativa de custo) -------------
    # Média MEDIDA no motor, por modelo×esforço. O que o motor nunca mediu cai na
    # prévia do A/B; o que não existe em nenhum dos dois cai na mediana das medidas.
    # Nada é inventado: cada régua tem procedência, e a procedência vai para o texto.
    com_token = [l for l in linhas_codex if l["tokens_total"] is not None]
    _por_modelo: dict[str, list[int]] = defaultdict(list)
    for l in com_token:
        _por_modelo[f"{l['model']}/{l['effort']}"].append(l["tokens_total"])
    medias = {k: sum(v) / len(v) for k, v in _por_modelo.items()}
    _tudo = sorted(l["tokens_total"] for l in com_token)
    mediana = float(_tudo[len(_tudo) // 2]) if _tudo else 0.0
    MEDIDAS.update({"medias": medias, "mediana": mediana,
                    "chamadas_medidas": len(com_token), "chamadas": len(linhas_codex),
                    "modelos": {k: ("motor" if k in medias else "previa")
                                for k in sorted(set(medias) | set(TOKENS_DA_PREVIA))}})

    def _regua(l) -> float:
        """Tokens por chamada a usar quando a chamada não tem medição."""
        k = f"{l['model']}/{l['effort']}"
        return medias.get(k, TOKENS_DA_PREVIA.get(k, mediana))

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
    # Além do custo (chamadas), o que o pacote RENDEU de avaliação: quantas
    # aprovações, quantas reprovações do aprovador e quantas chamadas nem chegaram a
    # ser avaliadas. E, separado do dado bruto, a atribuição de causa (curada).
    resumo_pacotes = []
    for sprint, tasks in por_sprint.items():
        for tid in sorted(tasks):
            c = tasks[tid]
            linhas_do_pacote = [l for l in linhas_codex
                                if l["sprint_id"] == sprint and l["task_id"] == tid]
            janelas = [CAUSA_TESTE_OU_PLANO.get((sprint, tid)),
                       CAUSA_TESTE_OU_PLANO_EXTRA.get((sprint, tid))]

            def _na_janela(linha) -> bool:
                """A chamada cai numa janela de culpa nossa (teste/plano)?"""
                return any(j and j[0][0] <= linha["attempt"] <= j[0][1]
                           for j in janelas)

            vereditos = Counter()
            reprov_janela = 0        # reprovações causadas pelo NOSSO teste/plano
            aprovada_em, modelo_aprovou = None, ""
            for i, l in enumerate(linhas_do_pacote, 1):
                rv = json.loads(l["review_result"] or "{}")
                v = rv.get("veredito")
                vereditos[v or "sem avaliação"] += 1
                if v in ("REQUEST_CHANGES", "REJECT") and _na_janela(l):
                    reprov_janela += 1
                if v == "APPROVE":
                    aprovada_em = i
                    modelo_aprovou = (f"{l['model']}/{l['effort']}" if l["model"]
                                      else "(sem modelo)")
            infra = sum(1 for l in linhas_do_pacote
                        if (l["failure_class"] or "") in CLASSES_INFRA)
            culpa, notas = 0, []
            for j in janelas:
                if not j:
                    continue
                (a, b), nota = j
                # a janela não conta de novo a chamada que nem chegou a rodar
                # (cota/crash): ela já está na coluna de infraestrutura, e contá-la
                # duas vezes inflava o desconto (zerava o "do modelo" da P09).
                culpa += sum(1 for l in linhas_do_pacote
                             if a <= l["attempt"] <= b
                             and (l["failure_class"] or "") not in CLASSES_INFRA)
                notas.append(f"{nota} (chamadas {a}–{b})")
            maximo = max(c)
            # ---- custo: medido (tokens reais) × estimado (régua do modelo) e,
            # dentro dele, ESPERADO × RETRABALHO. `esperado` = custo da PRIMEIRA
            # chamada do pacote (a tentativa de acertar de primeira); `retrabalho` =
            # todo o resto — cada chamada extra existe porque a anterior não passou.
            # O múltiplo daqui é em DINHEIRO; o da Tabela 1 é em contagem de
            # reprovações, então os dois não têm de coincidir.
            primeira = min(c)
            pri = [l for l in linhas_do_pacote if l["attempt"] == primeira]
            resto = [l for l in linhas_do_pacote if l["attempt"] != primeira]

            def _custo(ls) -> tuple[float, float, int, int]:
                """(US$ medido, US$ estimado, tokens medidos, chamadas) de um grupo."""
                med = sum(l["tokens_total"] or 0 for l in ls) * USD_POR_TOKEN
                est = sum(_regua(l) for l in ls
                          if l["tokens_total"] is None) * USD_POR_TOKEN
                return med, est, sum(l["tokens_total"] or 0 for l in ls), len(ls)

            e_med, e_est, e_tok, e_n = _custo(pri)
            r_med, r_est, r_tok, r_n = _custo(resto)
            _sobra = (e_med + e_est + r_med + r_est) - (
                sum(l["tokens_total"] or 0 for l in linhas_do_pacote) * USD_POR_TOKEN
                + sum(_regua(l) for l in linhas_do_pacote
                      if l["tokens_total"] is None) * USD_POR_TOKEN)
            if abs(_sobra) > 1e-9:      # esperado + retrabalho TEM de dar o total
                raise AssertionError(
                    f"custo de {sprint}/{tid} não fecha: sobra {_sobra:.6f} US$")
            resumo_pacotes.append({
                "sprint": sprint, "task": tid, "chamadas": sum(c.values()),
                "max_tentativa": maximo, "min_tentativa": min(c),
                "titulo": titulos.get(tid, ""),
                "aprovacoes": vereditos.get("APPROVE", 0),
                "reprovacoes": (vereditos.get("REQUEST_CHANGES", 0)
                                + vereditos.get("REJECT", 0)),
                "sem_avaliacao": vereditos.get("sem avaliação", 0),
                "reprov_em_janela": reprov_janela,
                "aprovada_na_chamada": aprovada_em,
                "modelo_que_aprovou": modelo_aprovou,
                "infra": infra,
                "culpa_teste_plano": culpa,
                "notas_culpa": notas,
                "do_modelo": sum(c.values()) - infra - culpa,
                "modelos": dict(Counter(
                    f"{l['model']}/{l['effort']}" for l in linhas_codex
                    if l["sprint_id"] == sprint and l["task_id"] == tid).most_common()),
                # ---- custo: medido (tokens reais) × estimado (régua do modelo) ----
                "tokens_medidos": sum(l["tokens_total"] or 0 for l in linhas_do_pacote),
                "chamadas_com_token": sum(1 for l in linhas_do_pacote
                                          if l["tokens_total"] is not None),
                "usd_medido": sum(l["tokens_total"] or 0
                                  for l in linhas_do_pacote) * USD_POR_TOKEN,
                "usd_estimado": sum(_regua(l) for l in linhas_do_pacote
                                    if l["tokens_total"] is None) * USD_POR_TOKEN,
                # esperado (1ª chamada) × retrabalho (2ª em diante), em US$ e em chamadas
                "chamadas_esperado": e_n, "tokens_esperado": e_tok,
                "usd_esperado": e_med + e_est, "usd_esperado_medido": e_med,
                "usd_esperado_estimado": e_est,
                "chamadas_retrabalho": r_n, "tokens_retrabalho": r_tok,
                "usd_retrabalho": r_med + r_est, "usd_retrab_medido": r_med,
                "usd_retrab_estimado": r_est,
            })

    # ---- outros agentes (não são chamadas do Codex) ---------------------------
    outros = Counter(f"{l['agent']}" for l in linhas if l["agent"] != "codex")

    # janela real do escopo (start_time é epoch em segundos, não texto)
    from datetime import datetime as _dtm          # o módulo importa isto localmente
    _ts = [l["start_time"] for l in linhas if l["start_time"]]
    _ini = _dtm.fromtimestamp(min(_ts)).strftime("%Y-%m-%d %H:%M") if _ts else ""
    _fim = _dtm.fromtimestamp(max(_ts)).strftime("%Y-%m-%d %H:%M") if _ts else ""

    dados = {
        "total_codex": total,
        "degraus": degraus,
        "chamadas_topo": sum(1 for l in linhas_codex if l["model"] == "gpt-6-astra"),
        "chamadas_baratas": sum(1 for l in linhas_codex
                                if l["model"] == "gpt-5.6-luna"),
        "chamadas_por_sprint_pacote": {s: {t: dict(c) for t, c in v.items()}
                                      for s, v in por_sprint.items()},
        "pacotes": resumo_pacotes,
        "outros_agentes": dict(outros),
        "maior_degrau": max((d["degrau"] for d in degraus), default=0),
        # janela real do que está no escopo (antes era literal, e citava a 002)
        "janela": (_ini, _fim),
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

    # só as sprints do escopo (o eixo NÃO pode listar pacote que ficou fora: coluna
    # vazia de 001/002 no meio do gráfico é ruído que parece dado)
    sprints = [s for s in SPRINTS if PACOTES.get(s)]
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

    _ps = dados["chamadas_por_sprint_pacote"]
    _inv = " · ".join(
        f"{s.split('-')[-1]}: {len(_ps.get(s, {}))} pacote(s) com chamada, "
        f"{sum(sum(c.values()) for c in _ps.get(s, {}).values())} chamada(s)"
        for s in SPRINTS if _ps.get(s))
    ax.annotate(f"{_inv}.\n{NOTA_CURTA}.",
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
                 f"{NOTA_CURTA}\n"
                 "(o modelo dominante de cada degrau; a lista completa está na tabela "
                 "do relatório)", fontsize=11.5, pad=14)
    ax.grid(axis="y", alpha=0.25, zorder=0)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_ylim(0, max(pct) * 1.25)
    ax.annotate(f"Cada barra é uma fatia das {dados['total_codex']} chamadas do Codex, "
                "não a chance de acerto.\n"
                "A taxa de sucesso por degrau está na tabela 2.",
                xy=(0.995, 0.95), xycoords="axes fraction", ha="right", va="top",
                fontsize=8, color="#555555",
                bbox=dict(boxstyle="round,pad=0.4", facecolor="#f5f5f5",
                          edgecolor="#cccccc"))
    destino = OUT / "distribuicao-tentativas.png"
    fig.savefig(destino, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return destino


def desenha_custo(dados: dict) -> Path:
    """Gráfico 3: custo ESTIMADO por pacote — medido × estimado na mesma barra.

    Sólido = token que o motor MEDIU naquela chamada (rodapé do Codex, P-10, de 28/09
    ~19h em diante). Hachurado = chamada sem medição, estimada pela régua de tokens do
    modelo. Pacote sem nenhuma medição fica 100% hachurado: a hachura é a parte
    estimada, não enfeite — misturar as duas sem marcar seria mentir sobre a precisão.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    # O título tem "US$" várias vezes e o matplotlib interpreta `$...$` como
    # matemática (ParseException no desenho). Aqui os cifrões são literais.
    plt.rcParams["text.parse_math"] = False

    pacotes = [p for p in dados["pacotes"] if p["chamadas"]]
    pacotes.sort(key=lambda p: (p["sprint"], p["task"]))
    sprints = sorted({p["sprint"] for p in pacotes})
    cores = dict(zip(sprints, ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]))

    total = sum(p["usd_esperado"] + p["usd_retrabalho"] for p in pacotes)
    esp = sum(p["usd_esperado"] for p in pacotes)
    ret = sum(p["usd_retrabalho"] for p in pacotes)
    rotulos = [f"{p['sprint'].split('-')[-1]}/{p['task']}" for p in pacotes]
    AZUL, VERM = "#1f77b4", "#c62828"      # esperado × retrabalho

    fig, ax = plt.subplots(figsize=(13.5, 6.4))
    for i, p in enumerate(pacotes):
        base = 0.0
        # DUAS leituras na mesma barra: a COR diz se o dinheiro era esperado (1ª
        # chamada) ou retrabalho (2ª em diante); o PADRÃO diz se aquele pedaço foi
        # MEDIDO pelo motor (sólido) ou estimado pela régua (hachurado).
        for usd_med, usd_est, cor in (
                (p["usd_esperado_medido"], p["usd_esperado_estimado"], AZUL),
                (p["usd_retrab_medido"], p["usd_retrab_estimado"], VERM)):
            if usd_med:
                ax.bar(i, usd_med, bottom=base, width=0.74, color=cor,
                       edgecolor="white", linewidth=0.5, zorder=3)
                base += usd_med
            if usd_est:
                ax.bar(i, usd_est, bottom=base, width=0.74, color="#f2f2f2",
                       edgecolor=cor, linewidth=0.9, hatch="////", zorder=3)
                base += usd_est
        valor = p["usd_esperado"] + p["usd_retrabalho"]
        if valor:
            ax.text(i, valor + (total * 0.012 if total else 0.002),
                    f"{valor:.3f}".replace(".", ","),
                    ha="center", va="bottom", fontsize=6.4, color="#333333", zorder=4)

    alto = max((p["usd_esperado"] + p["usd_retrabalho"] for p in pacotes), default=0.05)
    for s in sprints:
        idx = [i for i, p in enumerate(pacotes) if p["sprint"] == s]
        gasto = sum(pacotes[i]["usd_esperado"] + pacotes[i]["usd_retrabalho"]
                    for i in idx)
        # Rótulo do sprint ABAIXO do eixo (transform do eixo x): dentro da área ele
        # brigava com os rótulos de valor das barras. O gráfico 1 usa o mesmo recurso.
        ax.text(sum(idx) / len(idx), -0.17, f"{s.split('-')[-1]}\nUS$ {gasto:.2f}",
                transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=8.5, fontweight="bold", color=cores[s])

    ax.set_xticks(range(len(pacotes)))
    ax.set_xticklabels(rotulos, fontsize=8)
    ax.set_ylabel("custo estimado (US$) — azul: esperado · vermelho: retrabalho",
                  fontsize=10)
    ax.set_title(
        "Coding_Machine — custo por pacote: esperado (1ª chamada) × retrabalho\n"
        f"{NOTA_CURTA}\n"
        f"assinatura US$ 20/mês · total estimado US$ {total:.2f} em "
        f"{dados['total_codex']} chamadas do Codex: esperado US$ {esp:.2f} "
        f"({100 * esp / total:.0f}%) · retrabalho US$ {ret:.2f} "
        f"({100 * ret / total:.0f}%"
        + (f", ×{total / esp:.1f} sobre o esperado)" if esp else ")"),
        fontsize=12, pad=14)
    ax.grid(axis="y", alpha=0.25, zorder=0)
    ax.set_axisbelow(True)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    ax.set_ylim(0, alto * 1.32)
    ax.legend(handles=[
        Patch(facecolor=AZUL, edgecolor="white",
              label="esperado (1ª chamada) — medido"),
        Patch(facecolor="#f2f2f2", edgecolor=AZUL, hatch="////",
              label="esperado (1ª chamada) — estimado"),
        Patch(facecolor=VERM, edgecolor="white",
              label="retrabalho (2ª em diante) — medido"),
        Patch(facecolor="#f2f2f2", edgecolor=VERM, hatch="////",
              label="retrabalho (2ª em diante) — estimado")],
        loc="upper left", fontsize=8, framealpha=0.95)

    destino = OUT / "custo-por-pacote.png"
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

    def _mult(a: int, b: int) -> str:
        """Reprovações por aprovação, em múltiplo: 2x = duas por aprovação.

        Não é porcentagem de nada — por isso o valor pode passar de 1x (e de
        '100%'), e por isso vai em x em vez de %.
        """
        if not b:
            return "—"
        r = a / b
        if r == 0:
            return "0x"
        if r < 0.05:
            return "<0,1x"
        if abs(r - round(r)) < 0.05:          # 1,0 / 2,0 / 3,0 → 1x / 2x / 3x
            return f"{round(r)}x"
        return f"{r:.1f}".replace(".", ",") + "x"

    def _retrab(p, ajustado: bool) -> str:
        """Múltiplo de retrabalho do pacote = reprovações ÷ aprovações.

        `ajustado` desconta as reprovações causadas pelo NOSSO teste/plano (a mesma
        janela curada da coluna `culpa teste/plano`) — nunca as do codificador.
        Pacote sem nenhuma aprovação fica '—' (o denominador não existiria).
        """
        aprov = p["aprovacoes"]
        if not aprov:
            return "—"
        reprov = p["reprovacoes"] - (p["reprov_em_janela"] if ajustado else 0)
        return _mult(reprov, aprov)

    linhas_pacote = []
    for s in SPRINTS:
        c = d["chamadas_por_sprint_pacote"].get(s, {})
        for tid in PACOTES.get(s, []):
            m = c.get(tid)
            if not m:
                continue
            p = next(x for x in d["pacotes"] if x["sprint"] == s and x["task"] == tid)
            tent = sorted(int(k) for k in m)
            mods = ", ".join(sorted(p["modelos"]))
            buraco = "" if tent == list(range(tent[0], tent[-1] + 1)) else " ⚠"
            aprov = (f"{p['aprovada_na_chamada']}ª ({p['modelo_que_aprovou']})"
                     if p["aprovada_na_chamada"] else "—")
            linhas_pacote.append(
                f"| {s.split('-')[-1]} | {tid} | {sum(m.values())} | "
                f"{p['aprovacoes']}/{p['reprovacoes']}/{p['sem_avaliacao']} | "
                f"{tent[0]}ª–{tent[-1]}ª{buraco} | {aprov} | "
                f"{p['infra']} | {p['culpa_teste_plano']} | {p['do_modelo']} | "
                f"{_retrab(p, False)} | {_retrab(p, True)} | "
                f"{mods} |")
    tabela_pacotes = "\n".join(linhas_pacote)

    # ---- retrabalho: números globais (bruto e ajustado, nas duas leituras) -----
    tot_aprov = sum(p["aprovacoes"] for p in d["pacotes"])
    tot_reprov = sum(p["reprovacoes"] for p in d["pacotes"])
    tot_reprov_jan = sum(p["reprov_em_janela"] for p in d["pacotes"])

    def _pct(a, b) -> str:
        return f"{100.0 * a / b:.1f}%" if b else "—"

    retrab_global = (
        f"No total: **{tot_reprov} reprovações ÷ {tot_aprov} aprovações** = "
        f"**{_mult(tot_reprov, tot_aprov)} bruto** e "
        f"**{_mult(tot_reprov - tot_reprov_jan, tot_aprov)} ajustado** (a escada cobrou "
        f"{tot_reprov_jan} reprovações que eram defeito do NOSSO teste/plano). "
        f"Se o denominador for *chamadas avaliadas* em vez de aprovações — "
        f"`reprov ÷ (aprov+reprov)`, aí sim uma fatia — os mesmos números ficam "
        f"{_pct(tot_reprov, tot_aprov + tot_reprov)} e "
        f"{_pct(tot_reprov - tot_reprov_jan, tot_aprov + tot_reprov - tot_reprov_jan)}."
    )

    # ---- desconto da culpa: o que NÃO era do modelo ---------------------------
    tot_infra = sum(p["infra"] for p in d["pacotes"])
    tot_culpa = sum(p["culpa_teste_plano"] for p in d["pacotes"])
    tot_modelo = sum(p["do_modelo"] for p in d["pacotes"])
    com_culpa = [p for p in d["pacotes"] if p["culpa_teste_plano"]]
    linhas_desconto = []
    for p in sorted(com_culpa, key=lambda x: -x["culpa_teste_plano"]):
        notas = "; ".join(p["notas_culpa"])
        linhas_desconto.append(
            f"| {p['sprint'].split('-')[-1]} | {p['task']} | {p['chamadas']} | "
            f"{p['culpa_teste_plano']} | {p['do_modelo']} | {notas} |")
    tabela_desconto = "\n".join(linhas_desconto) or "| — | — | — | 0 | — | — |"

    # ---- valor marginal da escada: em que chamada a aprovação veio ------------
    faixas = [(1, 1, "1ª chamada"), (2, 3, "2ª–3ª"), (4, 5, "4ª–5ª"),
              (6, 10, "6ª–10ª"), (11, 15, "11ª–15ª")]
    linhas_marg = []
    for a, b, rot in faixas:
        grupo = [p for p in d["pacotes"]
                 if p["aprovada_na_chamada"] and a <= p["aprovada_na_chamada"] <= b]
        models = sorted({p["modelo_que_aprovou"] for p in grupo})
        linhas_marg.append(f"| {rot} | {len(grupo)} | "
                           f"{', '.join(p['task'] + ' (' + p['sprint'].split('-')[-1] + ')' for p in grupo) or '—'} | "
                           f"{', '.join(models) or '—'} |")
    tabela_marginal = "\n".join(linhas_marg)

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
    # ---- custo: os números do texto saem da MESMA agregação do gráfico 3 ------
    custo_total = sum(p["usd_medido"] + p["usd_estimado"] for p in d["pacotes"])
    custo_medido = sum(p["usd_medido"] for p in d["pacotes"])
    # esperado × retrabalho — mesma agregação que colore o gráfico 3
    custo_esp = sum(p["usd_esperado"] for p in d["pacotes"])
    custo_ret = sum(p["usd_retrabalho"] for p in d["pacotes"])
    n_esp = sum(p["chamadas_esperado"] for p in d["pacotes"])
    n_ret = sum(p["chamadas_retrabalho"] for p in d["pacotes"])
    mult_dinheiro = (custo_total / custo_esp) if custo_esp else 0.0
    _br = lambda v: f"{v:.2f}".replace(".", ",")          # US$ no padrão pt-BR
    _br1 = lambda v: f"{v:.1f}".replace(".", ",")
    _j = d["janela"]

    def _dt(s: str) -> str:                     # 2026-09-28 08:12 -> 28/09/2026 08:12
        return f"{s[8:10]}/{s[5:7]}/{s[:4]} {s[11:16]}" if len(s) >= 16 else s
    pct_esp = f"{100 * custo_esp / custo_total:.0f}%" if custo_total else "—"
    pct_ret = f"{100 * custo_ret / custo_total:.0f}%" if custo_total else "—"
    _piores = sorted((p for p in d["pacotes"] if p["usd_retrabalho"] > 0),
                     key=lambda p: -p["usd_retrabalho"])[:6]
    bloco_piores = (
        "Pacotes que mais gastaram insistindo:\n\n"
        "| pacote | sprint | chamadas | esperado (US$) | retrabalho (US$) | múltiplo |\n"
        "|---|---|---|---|---|---|\n"
        + "\n".join(
            f"| {p['task']} | {p['sprint'].split('-')[-1]} | {p['chamadas']} | "
            f"{p['usd_esperado']:.3f} | {p['usd_retrabalho']:.3f} | "
            f"{(p['usd_esperado'] + p['usd_retrabalho']) / p['usd_esperado']:.1f}x |"
            .replace(".", ",")
            for p in _piores)
        + f"\n\nEm chamadas: **{n_esp}** foram a tentativa de acertar de primeira e "
          f"**{n_ret}** foram retrabalho.")
    _top = sorted(d["pacotes"], key=lambda p: -(p["usd_medido"] + p["usd_estimado"]))[:3]
    top_custo_txt = "; ".join(
        f"**{p['task']}** (sprint {p['sprint'].split('-')[-1]}) US$ "
        f"{p['usd_medido'] + p['usd_estimado']:.3f}".replace(".", ",")
        for p in _top)
    _n_med = MEDIDAS.get("chamadas_medidas", 0)
    cobertura = (f"{_n_med} das {total} chamadas ({100.0 * _n_med / total:.1f}%)"
                 if total else "nenhuma chamada")
    _modelos_motor = [k for k, v in (MEDIDAS.get("modelos") or {}).items() if v == "motor"]
    _modelos_previa = [k for k, v in (MEDIDAS.get("modelos") or {}).items() if v == "previa"]

    # ---- objetivos dos pacotes (todas as sprints planejadas até agora) --------
    linhas_objetivo = []
    for sprint, pacotes in objetivos_por_sprint().items():
        for tid, titulo in pacotes:
            linhas_objetivo.append(f"| {sprint.split('-')[-1]} | {tid} | {titulo} |")
    tabela_objetivos = "\n".join(linhas_objetivo) or "| — | — | — |"

    hoje = datetime.now().strftime("%d/%m/%Y")

    md = f"""# Chamadas de API do Codex no Coding_Machine

**Pergunta:** quantas chamadas de API o motor autônomo gastou por pacote do backlog, em
que tentativa cada uma aconteceu e com que modelo.
**Fonte:** `.autodev/state.db`, tabela `attempts` (o próprio motor grava uma linha por
invocação).
**Janela:** {_dt(_j[0])} a {_dt(_j[1])} —
**sprints 003 e 004**, as únicas no protocolo de registro de hoje (aviso 2).
**Data do relatório:** {hoje}.
**Total no período:** **{total} chamadas do Codex**, {aprovadas} delas aprovadas
(revisão + integração).

## Avisos

1. **Chamada é custo — com a procedência declarada.** Cada linha conta **uma invocação** do
   agente. O motor passou a **gravar tokens** em 28/09 (P-10, lendo o rodapé do agente):
   das {total} chamadas no escopo, **{MEDIDAS.get('chamadas_medidas', 0)} têm token medido** e
   as outras foram **estimadas** pela régua do modelo. Todo valor em US$ diz de qual dos
   dois vem — sólido é medição, hachurado é estimativa.
2. **Por que só as sprints 003 e 004.** {NOTA_PROTOCOLO}
3. **A 003 ainda está em execução.** Entrou em 28/09 22:58 e tem 1 pacote integrado de 7 (a
   P01 fechou em 6 chamadas, aprovada no 5º degrau, `astra/low`) com a P02 rodando; os outros
   5 ainda não começaram. Os números dela **mudam a cada rodada** — este documento é uma
   foto do momento, não um fechamento.
4. **A sprint 004 está fechada** (4/4 integradas em 28/09) — os números dela não mudam mais.
5. **"Nª tentativa" não é o degrau da escada de modelos — são dois contadores.** O número
   nas tabelas é a **chamada** (`attempt`, sequência do banco, sempre `max+1`); o modelo vem
   do **contador da task** (`tentativas`), pelo mapa `1ª→luna/low … 5ª+→astra/low`. Quando o
   contador é reiniciado (rearme por dependência integrada, reabertura por defeito de
   contrato) ele **volta ao degrau barato** enquanto a numeração da chamada continua — é por
   isso que existe `luna/low` numa 7ª chamada. O degrau mede "quantas vezes chamou", não
   "quão forte era o modelo".
   Nas falhas de infraestrutura (`CODEX_QUOTA`, `NETWORK_ERROR`, `ENVIRONMENT_ERROR`…), a
   política declara `classes_sem_escalonamento` — mas **essa lista não chega à escolha do
   modelo**: o orquestrador usa o mapa do contador e ignora o tier da decisão. Na prática,
   espera de cota escalona como qualquer falha. É um defeito de fiação, não uma intenção.
6. **A partir da 5ª chamada o mapa satura no topo** (`min(tentativa, 5)` → `astra/low`):
   degraus 5 a 15 repetem o mesmo modelo. No período isso **não** virou desperdício: são
   {d['chamadas_topo']} chamadas no topo ({100.0 * d['chamadas_topo'] / d['total_codex']:.1f}%) contra
   {d['chamadas_baratas']} no degrau mais barato ({100.0 * d['chamadas_baratas'] / d['total_codex']:.1f}%) — a
   cauda é curta porque a maioria dos pacotes aprovou antes do 5º degrau (tabela 3).
7. **{len(fora_escada)} combinação(ões) fora da escada declarada:** {', '.join(f'`{x}`' for x in fora_escada) or 'nenhuma'}.
   As combinações `…/medium` de 27/09 saíram junto com as sprints 001/002 (foi o dia em que
   a escada foi padronizada); nas 003 e 004 a escada é seguida à risca.
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
- **{tot_culpa} das {total} chamadas ({100.0 * tot_culpa / total:.1f}%) foram gastas por defeito do
  nosso teste/plano**, e {tot_infra} ({100.0 * tot_infra / total:.1f}%) por infraestrutura (cota/crash).
  Descontadas, sobram **{tot_modelo} chamadas ({100.0 * tot_modelo / total:.1f}%)** atribuíveis ao
  trabalho do modelo — o denominador honesto para comparar modelos (seção "Descontando").
- **Onde a escada se paga (tabela 3):** {sum(1 for p in d['pacotes'] if p['aprovada_na_chamada'] and p['aprovada_na_chamada'] <= 3)} pacote(s) aprovaram até a 3ª
  chamada; {sum(1 for p in d['pacotes'] if p['aprovada_na_chamada'] and 4 <= p['aprovada_na_chamada'] <= 5)} na 4ª–5ª; {sum(1 for p in d['pacotes'] if p['aprovada_na_chamada'] and p['aprovada_na_chamada'] >= 6)} da 6ª em diante.
  O degrau caro (`astra/low`) assinou {sum(1 for p in d['pacotes'] if p['modelo_que_aprovou'].startswith('gpt-6-astra'))} aprovação(ões) —
  sempre em pacote que carregava, junto, defeito de contrato nosso.

## Gráfico 1 — chamadas por pacote, empilhadas pela tentativa

![Chamadas de API do Codex por pacote do backlog, empilhadas pelo número da tentativa](report/histograma-tentativas.png)

Cada coluna é um pacote; a altura é quantas vezes o Codex foi chamado nele; a cor diz
**em que altura da escada** a chamada aconteceu (verde = cedo, vermelho = fim da
escada). Total: {total} chamadas.

## Tabela 1 — por pacote

`chamadas` é o custo; `aprov./reprov./s/aval.` são as **avaliações do modelo aprovador**
naquele pacote (aprovado / reprovado / chamadas que nem chegaram a ser avaliadas);
`aprovada na` diz **em que chamada** (e com que modelo) a aprovação saiu; `infra` e
`culpa teste/plano` separam o que **não era do modelo** (cota/crash e defeito de
teste/plano, atribuição curada descrita abaixo); `do modelo` é o que sobra.

`retrab. bruto` = **reprovações do aprovador ÷ aprovações do aprovador**, em **múltiplo**:
`2x` significa duas reprovações para cada aprovação entregue (não é porcentagem de nada —
pode passar de 1x, e é por isso que vai em `x` e não em `%`); `retrab. ajust.` desconta as
reprovações que foram culpa do **nosso teste/plano** — nunca as do codificador. Pacote sem
aprovação nenhuma fica `—`. {retrab_global}

| sprint | pacote | chamadas | aprov./reprov./s/aval. | chamadas (1ª–última) | aprovada na | infra | culpa teste/plano | do modelo | retrab. bruto | retrab. ajust. | modelos usados |
|---|---:|---:|---|---|---:|---:|---:|---:|---:|---:|---|
{tabela_pacotes}

## Tabela 3 — em que chamada a aprovação veio

O valor marginal da escada: onde os pacotes **efetivamente** destravaram. É esta tabela
que decide se a 4ª/5ª posição da escada se paga.

| aprovada na | pacotes | quais | modelo que aprovou |
|---|---:|---|---|
{tabela_marginal}

## Descontando o que não era do modelo

A classe da falha o motor grava; **de quem era a culpa, não**. As janelas abaixo foram
atribuídas à mão, olhando os logs e as decisões — e por isso aparecem em coluna separada,
nunca no lugar do dado bruto. Sem esse desconto, qualquer comparação entre modelos cobra
do agente o defeito do nosso teste.

| sprint | pacote | chamadas | culpa teste/plano | do modelo | decisão que descreve |
|---|---:|---:|---:|---:|---|
{tabela_desconto}

No total: **{tot_culpa} chamadas ({100.0 * tot_culpa / total:.1f}%)** foram gastas por defeito do
nosso teste/plano e **{tot_infra} ({100.0 * tot_infra / total:.1f}%) por infraestrutura** (cota, crash).
Sobram **{tot_modelo} chamadas ({100.0 * tot_modelo / total:.1f}%)** atribuíveis ao trabalho do modelo —
esse é o único denominador honesto para comparar modelos.


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

1. **Contador da task ≠ número da chamada** (aviso 5) — a explicação principal. O contador
   reinicia no rearme/reabertura e volta ao degrau barato enquanto a chamada continua sendo
   numerada. Isso é **desejado**: nos rearames de 28/09 a P01 e a P04 voltaram ao degrau 1 e
   subiram de novo — gastaram barato até acertar, em vez de continuar no topo.
2. **Falha de infraestrutura hoje ESCALONA** (aviso 5): a lista
   `classes_sem_escalonamento` existe na política, mas o orquestrador escolhe o modelo pelo
   contador e ignora o tier da decisão. A intenção declarada ("espera de cota não gasta
   degrau") **não está fiada no código** — defeito registrado como pendência.
3. **Saturação depois da 5ª** (aviso 6): o mapa tem 5 entradas, então degraus ≥5 usam
   `astra/low`. No período o topo aparece em {d['chamadas_topo']} das {total} chamadas.
4. **Buracos na numeração** (aviso 8) e **5 chamadas anteriores à padronização** da própria
   escada (aviso 7).

**A pergunta de política que fica:** escalonar por **número** (é o que existe) ou por
**causa** — só escalar quando o teste do código falhar ou o revisor reprovar, e reiniciar no
degrau barato quando a falha foi de infraestrutura/harness. A tabela 3 dá a medida de que
lado pesa: os pacotes que aprovaram até a 3ª chamada mostram quanto trabalho se resolve sem
sair do degrau mais barato.

## Gráfico 3 — custo por pacote: esperado × retrabalho

![Custo por pacote: azul = esperado (1ª chamada), vermelho = retrabalho; sólido = token medido pelo motor, hachurado = estimativa](report/custo-por-pacote.png)

**Duas leituras na mesma barra.** A **cor** diz se o dinheiro era **esperado** (a 1ª chamada
do pacote, a tentativa de acertar de primeira) ou **retrabalho** (da 2ª em diante: cada
chamada extra existe porque a anterior não passou). O **padrão** diz se aquele pedaço foi
**medido** pelo motor (sólido) ou **estimado** pela régua do modelo (hachurado).

**Esperado × retrabalho.** Do total de US$ {_br(custo_total)}, **US$ {_br(custo_esp)}
({pct_esp}) era esperado** e **US$ {_br(custo_ret)} ({pct_ret}) é retrabalho** — o mesmo
backlog custaria **×{_br1(mult_dinheiro)} menos** se todo pacote passasse de primeira. O
múltiplo aqui é em **dinheiro**; o da Tabela 1 é em **contagem de reprovações**, e os dois
não têm de coincidir (pacote que reprova muito com modelo barato pesa pouco em dólar, e
vice-versa).

{bloco_piores}

**O que é medido e o que é estimado.** O motor só começou a gravar tokens em 28/09 (P-10,
lendo o rodapé do Codex): **{cobertura}** têm token medido. A parte **sólida** da barra é
essa medição; a **hachurada** são as chamadas sem medição, estimadas pela régua de tokens
por chamada do modelo — a média **medida no próprio motor** para aquele modelo/esforço
({len(_modelos_motor)} modelo(s)) e, só para o que o motor nunca viu, a prévia do A/B de
28/09 ({len(_modelos_previa)} modelo(s)). Chamada sem régua nenhuma usa a **mediana** das
medidas ({MEDIDAS.get('mediana', 0):,.0f} tokens). Nada aqui vem de tabela de preço de terceiro.

**O preço é o da sua assinatura:** US$ 20/mês = 4 blocos semanais de 100% → **US$ 0,05 por
ponto da janela semanal**; a régua medida é 118.096 tokens por ponto → **US$ 0,423 por
milhão de tokens**. Pela janela de 5h a leitura daria US$ 0,37/Mtok (as duas estão no
relatório de eficiência; a semanal é a que limita).

**Total estimado: US$ {_br(custo_total)}** para as {total} chamadas do Codex — sendo
**US$ {_br(custo_medido)} de token medido** e o resto estimativa. Os pacotes mais caros:
{top_custo_txt}.

## Objetivos dos pacotes (todas as sprints planejadas até agora)

Uma linha por pacote, com o objetivo como está no `dag.json` de cada sprint — lido do
**plano**, não escrito à mão. É o mapa do que cada pacote do backlog pedia, para ler as
tabelas acima sabendo o que estava sendo pedido em cada um.

::: {{.tabela-objetivos}}
| sprint | pacote | objetivo (título do pacote no plano) |
|---|---|---|
{tabela_objetivos}
:::

## Arquivos gerados e proveniência

| arquivo | o que é |
|---|---|
| `REPORT-TENTATIVAS-CODEX.md` | este relatório |
| `report/histograma-tentativas.png` | gráfico 1 |
| `report/distribuicao-tentativas.png` | gráfico 2 |
| `report/custo-por-pacote.png` | gráfico 3 (custo estimado: medido × estimado) |
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
    for p in desenha_histograma(dados), desenha_distribuicao(dados), desenha_custo(dados):
        print("PNG:", p)
    print("MD:", escreve_relatorio(dados))
