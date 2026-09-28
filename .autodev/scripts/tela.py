#!/usr/bin/env python3
"""Tela permanente de eventos do Coding_Machine. Somente leitura.

Uma linha curta por evento, em ordem inversa (o mais novo em cima), no estilo de um
`tail -f` legível. A fonte é o próprio motor: tabela `events` do state.db, mais o
estado atual (tasks, attempts, writer_lock, resource_waits) no cabeçalho.

    .autodev/scripts/tela.py                # ao vivo, atualiza a cada 2s
    .autodev/scripts/tela.py --once         # um quadro só (para log/pipe)
    .autodev/scripts/tela.py --intervalo 5 --linhas 25
    .autodev/scripts/tela.py --sprint DEVFACTORY-002

Sai com Ctrl-C. Sem dependências fora da biblioteca padrão.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
DB = RAIZ / ".autodev" / "state.db"
JANELA_RODADA_VIVA = 900          # heartbeat do writer_lock por 15 min = rodada viva

# um quadro tem: 2 linhas de cabeçalho + régua + feed + régua + rodapé
LINHAS_FIXAS = 6


# --------------------------------------------------------------------- cores
class Cor:
    def __init__(self, ligado: bool):
        self.ligado = ligado

    def __call__(self, texto: str, *codigos: str) -> str:
        if not self.ligado or not codigos:
            return texto
        return "".join(f"\033[{c}m" for c in codigos) + texto + "\033[0m"


C = Cor(False)          # trocado em main() conforme tty/--sem-cor
DIM, NEG, VERDE, AMARELO, VERMELHO, CIANO, MAGENTA = (
    "2", "1", "32", "33", "31", "36", "35")
ROSA = "95"


# ------------------------------------------------------------------- banco
def _con() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def sprint_padrao(con: sqlite3.Connection) -> str | None:
    linha = con.execute(
        "SELECT sprint_id FROM tasks GROUP BY sprint_id "
        "ORDER BY MAX(atualizado_em) DESC LIMIT 1").fetchone()
    return linha["sprint_id"] if linha else None


def _json(txt: str | None) -> dict:
    try:
        return json.loads(txt) if txt else {}
    except (ValueError, TypeError):
        return {}


def _hhmm(ts: float | None) -> str:
    if not ts:
        return "??:??"
    return datetime.fromtimestamp(float(ts)).strftime("%H:%M")


def _hora(ts: float) -> str:
    d = datetime.fromtimestamp(float(ts))
    hoje = datetime.now().date()
    return d.strftime("%H:%M:%S") if d.date() == hoje else d.strftime("%d/%m %H:%M")


# --------------------------------------------------- uma linha por evento
def linha_do_evento(tipo: str, task: str, payload: dict,
                    test_result: dict | None = None,
                    review_result: dict | None = None) -> tuple[str, str] | None:
    """Devolve (texto, cor) — ou None quando o evento é ruído para a tela.

    Função pura para poder ser testada sem banco.
    """
    t = payload or {}
    tr = test_result or {}
    rr = review_result or {}

    if tipo == "dag_carregado":
        return (f"rodada iniciada · {t.get('tasks', '?')} tasks no plano", DIM)

    if tipo == "tentativa_iniciada":
        modelo = " ".join(x for x in [t.get("model"), t.get("effort")] if x)
        agente = t.get("agent") or "?"
        return (f"t{t.get('attempt', '?')} codificação e teste iniciados · "
                f"{agente} {modelo}".rstrip(), CIANO)

    if tipo == "tentativa_finalizada":
        att = t.get("attempt", "?")
        status = t.get("status")
        classe = t.get("failure_class") or ""
        if status == "WAITING_RESOURCE" or classe == "CODEX_QUOTA":
            return (f"t{att} cota esgotada · espera de recurso agendada", AMARELO)
        if status == "OK":
            n = tr.get("passed")
            extra = f" · testes ok ({n})" if n is not None else ""
            return (f"t{att} desenvolvimento e testes finalizados{extra}", VERDE)
        if classe == "TEST_FAILURE":
            passou, falhou = tr.get("passed"), tr.get("failed")
            if falhou is None:
                detalhe = "testes falharam"
            else:
                detalhe = f"testes falharam ({passou} ok, {falhou} com falha)"
            return (f"t{att} {detalhe}", VERMELHO)
        if classe == "REVIEW_FAILURE":
            revisor = rr.get("revisor") or rr.get("modelo") or "revisor"
            n = len(rr.get("findings") or [])
            extra = f" · {revisor} ({n} achado{'s' if n != 1 else ''})" if rr else ""
            return (f"t{att} revisão reprovada{extra}", AMARELO)
        if classe == "SEM_ENTREGA":
            return (f"t{att} agente não entregou mudança", VERMELHO)
        return (f"t{att} falhou · {classe or status or 'sem classe'}", VERMELHO)

    if tipo == "integrado":
        commit = (t.get("commit") or "")[:8]
        ok = t.get("merge_ok")
        return (f"integrada · commit {commit}" + ("" if ok else " · MERGE COM RESSALVA"),
                VERDE + ";" + NEG)

    if tipo == "espera_recurso":
        return (f"aguardando cota até {_hhmm(t.get('retry_after'))}", AMARELO)

    if tipo == "bloqueado":
        return (f"BLOQUEADA · {t.get('motivo', '')}", VERMELHO + ";" + NEG)

    if tipo == "reaberto":
        antes, agora = t.get("tentativas_antes"), t.get("tentativas")
        passo = f"tentativas {antes}→{agora}" if antes is not None else "reaberta"
        return (f"reaberta ({passo}) · {str(t.get('motivo', ''))[:70]}", AMARELO)

    if tipo == "rearmado_por_dependencia":
        deps = ", ".join(t.get("deps_integradas") or [])
        return (f"rearmada (dependências integradas: {deps})", CIANO)

    if tipo == "worktree_realinhado":
        velha, nova = (t.get("base_antiga") or "?")[:7], (t.get("base_nova") or "?")[:7]
        return (f"worktree realinhado na base atual ({velha}→{nova})", DIM)

    if tipo == "sprint_reaberto":
        return (f"sprint reaberto ({t.get('motivo', '')})", DIM)

    if tipo == "sprint_transicao":
        return (f"SPRINT {t.get('de', '?')} → {t.get('para', '?')}", NEG)

    if tipo == "sprint_encerrado":
        resumo = str(t.get("resumo", ""))[:70]
        return (f"SPRINT {t.get('resultado', '?')} · {resumo}", VERDE + ";" + NEG)

    if tipo == "haq_criado":
        return (f"{t.get('haq_id', 'HAQ')} criada · depende de você", ROSA)

    if tipo == "haq_resolvido":
        return (f"{t.get('haq_id', 'HAQ')} resolvida", VERDE)

    if tipo == "contrato_corrigido":
        return (f"contrato do plano corrigido · {str(t.get('defeito', ''))[:60]}", MAGENTA)

    if tipo == "reinicio_por_bug_do_motor":
        return (f"tentativas devolvidas — bug do motor ({t.get('decisao', '')})", MAGENTA)

    if tipo == "task_concluida_por_evidencia":
        return ("concluída por evidência retroativa", VERDE)

    if tipo == "evidencia_corrigida":
        return ("evidência corrigida", DIM)

    if tipo == "transicao":
        para = t.get("para")
        if para in ("RETRY", "EM_VERIFICACAO", "FIM", "INTEGRATED", "DONE"):
            return (f"→ {para}", DIM)
        return None                       # QUEUED/RUNNING já aparecem como tentativa

    if tipo == "estado_forcado":
        return (f"estado forçado → {t.get('para', '?')}", DIM)

    return (f"{tipo} {json.dumps(t, ensure_ascii=False)[:60]}", DIM)


def eventos(con: sqlite3.Connection, sprint: str, quantos: int,
            tudo: bool = False) -> list[tuple[float, str, str, str]]:
    """[(ts, task, texto, cor)] do mais novo para o mais antigo."""
    linhas = con.execute(
        "SELECT ts, COALESCE(task_id,'') task_id, tipo, payload FROM events "
        "WHERE sprint_id=? ORDER BY ts DESC LIMIT ?", (sprint, quantos * 4)).fetchall()

    # busca sob demanda os detalhes de tentativa (testes/revisão) para enriquecer
    cache: dict[tuple[str, int], sqlite3.Row] = {}

    def _tentativa(task: str, att) -> sqlite3.Row | None:
        try:
            chave = (task, int(att))
        except (TypeError, ValueError):
            return None
        if chave not in cache:
            cache[chave] = con.execute(
                "SELECT test_result, review_result FROM attempts "
                "WHERE sprint_id=? AND task_id=? AND attempt=?", (sprint, *chave)
            ).fetchone()
        return cache[chave]

    saida = []
    for ev in linhas:
        payload = _json(ev["payload"])
        tr = rr = None
        if ev["tipo"] == "tentativa_finalizada":
            detalhe = _tentativa(ev["task_id"], payload.get("attempt"))
            if detalhe:
                tr = _json(detalhe["test_result"])
                rr = _json(detalhe["review_result"])
        descrito = linha_do_evento(ev["tipo"], ev["task_id"], payload, tr, rr)
        if descrito is None and not tudo:
            continue
        if descrito is None:
            descrito = (f"{ev['tipo']} (filtrado)", DIM)
        saida.append((ev["ts"], ev["task_id"] or "", descrito[0], descrito[1]))
        if len(saida) >= quantos:
            break
    return saida


# ------------------------------------------------------------- cabeçalho
def _rodada_viva(con: sqlite3.Connection) -> bool:
    agora = time.time()
    try:
        linhas = con.execute(
            "SELECT pid, heartbeat FROM writer_lock").fetchall()
    except sqlite3.OperationalError:
        return False
    for linha in linhas:
        pid, hb = linha["pid"] or 0, linha["heartbeat"] or 0
        if pid:
            try:
                os.kill(int(pid), 0)
                return True
            except (OSError, ValueError):
                pass
        if agora - float(hb) < JANELA_RODADA_VIVA:
            return True
    return False


def cabecalho(con: sqlite3.Connection, sprint: str, largura: int) -> list[str]:
    tot = con.execute("SELECT COUNT(*) n FROM tasks WHERE sprint_id=?", (sprint,)).fetchone()["n"]
    feitas = con.execute(
        "SELECT COUNT(*) n FROM tasks WHERE sprint_id=? AND estado='INTEGRATED'",
        (sprint,)).fetchone()["n"]
    sp = con.execute("SELECT estado FROM sprint_state WHERE sprint_id=?",
                     (sprint,)).fetchone()
    estado = sp["estado"] if sp else "?"

    viva = _rodada_viva(con)
    espera = con.execute(
        "SELECT retry_after FROM resource_waits WHERE sprint_id=? AND resolvido=0 "
        "ORDER BY retry_after LIMIT 1", (sprint,)).fetchone()
    agora = time.time()
    if espera and float(espera["retry_after"]) > agora:
        situacao = C(f"aguardando cota até {_hhmm(espera['retry_after'])}", AMARELO)
    else:
        situacao = C("rodada viva", VERDE) if viva else C("parada", DIM)

    pct = f"{feitas}/{tot}"
    l1 = (f" {C(sprint, NEG)}  {estado}  {C(pct + ' integradas', VERDE)}  {situacao}")

    # quem está na frente agora
    frente = con.execute(
        "SELECT task_id, estado, tentativas, tier_atual FROM tasks "
        "WHERE sprint_id=? AND estado IN "
        "('RUNNING','QUEUED','WAITING_RESOURCE','RETRY','BLOCKED') "
        "ORDER BY CASE estado WHEN 'RUNNING' THEN 0 WHEN 'RETRY' THEN 1 "
        "WHEN 'WAITING_RESOURCE' THEN 2 WHEN 'QUEUED' THEN 3 ELSE 4 END, task_id "
        "LIMIT 3", (sprint,)).fetchall()
    pedacos = []
    for f in frente:
        att = con.execute(
            "SELECT attempt, agent, model, effort FROM attempts "
            "WHERE sprint_id=? AND task_id=? ORDER BY attempt DESC LIMIT 1",
            (sprint, f["task_id"])).fetchone()
        modelo = ""
        if att:
            modelo = " ".join(x for x in [att["agent"], att["model"], att["effort"]] if x)
        numero = f"t{att['attempt']}" if att else f"t{f['tentativas']}"
        pedacos.append(f"{f['task_id']} {f['estado'].lower()}"
                       f" ({numero}{', ' + modelo if modelo else ''})")
    fila = " | ".join(pedacos) if pedacos else "nada na fila"
    l2 = " " + C(f"fila: {fila}  ·  degrau ate 5 tentativas por task", DIM)
    return [l1[:largura], l2[:largura]]


def quadro(con: sqlite3.Connection, sprint: str, linhas: int,
           tudo: bool, largura: int) -> str:
    saida = cabecalho(con, sprint, largura)
    saida.append(" " + C("─" * max(10, largura - 2), DIM))
    for ts, task, texto, cor in eventos(con, sprint, linhas, tudo):
        linha = f" {_hora(ts)}  {task:>4}  {texto}"
        saida.append(linha[:largura] if not C.ligado else _corta(linha, largura, cor))
    saida.append(" " + C("─" * max(10, largura - 2), DIM))
    saida.append(" " + C("Ctrl-C sai · atualiza a cada N s · eventos do motor (state.db)",
                         DIM))
    return "\n".join(saida)


def _corta(linha: str, largura: int, cor: str) -> str:
    """Corta preservando a cor (o code ANSI ocupa espaço no len)."""
    return C(linha[:largura], *cor.split(";"))


# ------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    global C
    p = argparse.ArgumentParser(description="Tela de eventos do Coding_Machine")
    p.add_argument("--sprint", default=None)
    p.add_argument("--linhas", type=int, default=0,
                   help="eventos no feed (0 = cabe na altura do terminal)")
    p.add_argument("--intervalo", type=float, default=2.0)
    p.add_argument("--tudo", action="store_true", help="inclui eventos de ruído")
    p.add_argument("--once", action="store_true", help="imprime um quadro e sai")
    p.add_argument("--sem-cor", action="store_true")
    args = p.parse_args(argv)

    ligado = (sys.stdout.isatty() and not args.sem_cor
              and not os.environ.get("NO_COLOR"))
    C = Cor(ligado)
    if not sys.stdout.isatty() or args.once:
        # em pipe/--once sai um quadro e sai (serve para log)
        pass

    if not DB.exists():
        print(f"state.db não encontrado em {DB}", file=sys.stderr)
        return 1

    def _um_quadro() -> str:
        con = _con()
        try:
            sprint = args.sprint or sprint_padrao(con)
            if not sprint:
                return " nenhum sprint no banco ainda"
            largura = max(60, shutil.get_terminal_size((100, 30)).columns)
            altura = shutil.get_terminal_size((100, 30)).lines
            linhas = args.linhas or max(5, altura - LINHAS_FIXAS)
            return quadro(con, sprint, linhas, args.tudo, largura)
        finally:
            con.close()

    if args.once or not sys.stdout.isatty():
        print(_um_quadro())
        return 0

    def _sai(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _sai)
    try:
        sys.stdout.write("\033[?25l")          # esconde o cursor
        while True:
            frame = _um_quadro()
            sys.stdout.write("\033[H\033[J" + frame + "\n")
            sys.stdout.flush()
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\n")        # devolve o cursor
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
