"""DEVFACTORY — interface de linha de comando.

Uso:
  python3 -m autodev detect                 # T01
  python3 -m autodev init                   # valida DAG e cria as tasks
  python3 -m autodev status                 # estado atual do Sprint
  python3 -m autodev run [--parar-em T07]   # executa o Sprint
  python3 -m autodev resume                 # recupera de crash e continua
  python3 -m autodev report                 # gera o relatorio
  python3 -m autodev stop <NIVEL> [alvo]    # kill switch
  python3 -m autodev start                  # limpa os kill switches
  python3 -m autodev haq                    # mostra a fila humana
  python3 -m autodev quota                  # esperas de cota registradas
  python3 -m autodev evidenciar T03 -e "..."  # conclui task por evidencia
  python3 -m autodev encerrar               # fecha o sprint (estado terminal)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SPRINT_PADRAO = "DEVFACTORY-001"


def _store(raiz: Path):
    from .state import StateStore
    return StateStore(raiz / ".autodev" / "state.db")


def _dt(ts) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "-"


def cmd_detect(args) -> int:
    from .agents import detectar
    from . import sandbox
    print("=== agentes detectados ===")
    for n, i in detectar().items():
        marca = "OK " if i.disponivel else "AUSENTE"
        print(f"  [{marca}] {n:8s} {i.versao or i.erro}  wrapper={i.wrapper or '-'}")
    print(f"\n=== sandbox ===")
    print(f"  bwrap: {'disponivel' if sandbox.disponivel() else 'AUSENTE'}")
    return 0


def cmd_init(args) -> int:
    from .config import Config, carrega_dag, ordem_topologica, valida_dag
    d = RAIZ / ".autodev" / "sprints" / args.sprint
    dag = json.loads((d / "dag.json").read_text())
    erros = valida_dag(dag)
    if erros:
        print("DAG INVALIDO:")
        for e in erros:
            print("  -", e)
        return 1
    ondas = ordem_topologica(dag)
    print(f"DAG valido: {len(dag['tasks'])} tasks em {len(ondas)} ondas")
    for i, o in enumerate(ondas):
        print(f"  onda {i}: {', '.join(o)}")
    with _store(RAIZ) as st:
        novas = st.criar_tasks_do_dag(args.sprint, dag)
        print(f"tasks criadas: {novas} (de {len(dag['tasks'])})")
    return 0


def cmd_status(args) -> int:
    from .config import carrega_dag, ordem_topologica
    with _store(RAIZ) as st:
        m = st.metricas(args.sprint)
        tasks = st.tasks(args.sprint)
        print(f"=== {args.sprint} ===")
        print(f"  tasks {m['tasks_total']}  done {m['done']}  blocked {m['blocked']}  "
              f"failed {m['failed']}  aguardando recurso {m['waiting_resource']}")
        print(f"  tentativas {m['tentativas_implementacao']}  esperas de cota "
              f"{m['esperas_cota']}  HAQ {m['haq']} (abertos {m['haq_abertos']})")
        ks = st.killswitch_ativo(args.sprint)
        if ks:
            print(f"  ⚠ KILL SWITCH ATIVO: {ks}")
        print()
        if args.verbose:
            for t in tasks:
                print(f"  {t['task_id']:5s} {t['estado']:17s} tent={t['tentativas']} "
                      f"cota={t['esperas_cota']} tier={t['tier_atual']} "
                      f"{t['agente'] or '-'}")
        st_ = st.ler_checkpoint(args.sprint)
        if st_:
            print(f"\n  ultimo checkpoint: {st_['estado']} "
                  f"em {_dt(st_['atualizado_em'])}")
    return 0


def _rodar_com_rodadas(o, args):
    """Roda o sprint em passadas: uma passada percorre as ondas UMA vez.

    Sem isto, a rodada acaba na primeira falha de integração (as dependentes ficam
    BLOCKED) e o autor precisa rodar de novo na mão a cada susto — foi assim que a
    primeira noite parou com 9 tasks bloqueadas. Cada passada extra rearma apenas
    as tasks bloqueadas por dependência JÁ integrada; se nada for rearmável, para
    na hora (nenhum custo por passada vazia). Não é laço infinito: o teto é o
    número de passadas pedido, e cada passada continua respeitando o limite de 5
    tentativas por task.
    """
    rodadas = max(1, getattr(args, "rodadas", 1) or 1)
    r = None
    for k in range(rodadas):
        # Um `run` terminado grava 'FIM' no sprint_state: é o fim de UMA rodada, não
        # do sprint — mas qualquer transição a partir de FIM é recusada pela máquina
        # de estados. Enquanto isso era comando manual, a passada seguinte nascia
        # morta; aqui ela simplesmente reabre (ENCERRADO/ABORTADO, que são decisão do
        # autor, não são tocados).
        store = getattr(o, "store", None)
        if store is not None and store.estado_sprint(o.sprint) == "FIM":
            store.reabrir_sprint(o.sprint, motivo=f"passada {k + 1} do run")
        if k:
            rearmadas = o.rearmar_dependentes()
            if not rearmadas:
                break
            print(f"rodada {k + 1}: {len(rearmadas)} task(s) rearmada(s) por "
                  f"dependencia integrada: {', '.join(rearmadas)}")
        r = o.rodar(parar_em=getattr(args, "parar_em", None))
        if r.parado_por:
            break
    return r


def cmd_run(args) -> int:
    from .orchestrator import Orquestrador
    import os
    os.environ.setdefault("AUTODEV_AGENT_TIMEOUT", "900")
    o = Orquestrador(RAIZ, args.sprint, modo_teste=args.modo_teste,
                     deadline_s=args.deadline)
    r = _rodar_com_rodadas(o, args)
    assert r is not None  # rodadas >= 1 sempre executa ao menos uma passada
    print(f"\n=== resumo ===\n  concluidas: {r.concluidas}/{len(r.tasks)}"
          f"\n  parado por: {r.parado_por or '-'}"
          f"\n  duracao: {r.duracao_s / 60:.1f} min")
    return 0 if not r.parado_por else 2


def cmd_resume(args) -> int:
    from .orchestrator import Orquestrador
    o = Orquestrador(RAIZ, args.sprint, modo_teste=args.modo_teste)
    rec = o.recuperar()
    print(f"recuperacao: {json.dumps(rec, ensure_ascii=False)}")
    r = o.rodar()
    print(f"concluidas: {r.concluidas}/{len(r.tasks)} | parado por: {r.parado_por or '-'}")
    return 0


def cmd_report(args) -> int:
    from .config import carrega_sprint
    from . import report as rp
    from .worktree import commit_atual
    d = RAIZ / ".autodev" / "sprints" / args.sprint
    sp = carrega_sprint(d / "sprint.yaml")
    with _store(RAIZ) as st:
        extra = {}
        f = d / "report-extras.json"
        if f.exists():
            extra = json.loads(f.read_text())
        txt = rp.gerar(st, args.sprint, objetivo=sp.get("objetivo", ""),
                       git_commit=commit_atual(RAIZ), **extra)
    destino = d / "SPRINT-REPORT.md"
    rp.escrever(txt, destino)
    print(f"relatorio escrito em {destino} ({len(txt)} chars)")
    return 0


def cmd_stop(args) -> int:
    from . import killswitch
    if args.nivel == "STOP_ALL":
        killswitch.ativar(RAIZ, args.nivel, " ".join(args.motivo) or "parada manual")
        print(f"arquivo .autodev/STOP escrito com {args.nivel}")
    with _store(RAIZ) as st:
        st.killswitch_set(args.nivel, True, alvo=args.alvo or "",
                          motivo=" ".join(args.motivo) or "parada manual")
    print(f"kill switch {args.nivel} {args.alvo or ''} ativado")
    return 0


def cmd_start(args) -> int:
    from . import killswitch
    killswitch.desativar(RAIZ)
    with _store(RAIZ) as st:
        st.killswitch_limpar()
    print("kill switches limpos")
    return 0


def cmd_haq(args) -> int:
    from . import haq
    with _store(RAIZ) as st:
        p = haq.escrever(st, args.sprint, RAIZ / ".autodev" / "sprints" /
                         args.sprint / "HAQ.md")
        print(p.read_text())
    return 0


def cmd_quota(args) -> int:
    with _store(RAIZ) as st:
        rows = st.conn.execute(
            "SELECT * FROM resource_waits WHERE sprint_id=? ORDER BY detectado_em",
            (args.sprint,)).fetchall()
        if not rows:
            print("nenhuma espera de cota registrada")
            return 0
        for r in rows:
            print(f"  {_dt(r['detectado_em'])} {r['task_id']:5s} {r['agent']:6s} "
                  f"retomar em {_dt(r['retry_after'])} "
                  f"({(r['retry_after'] - r['detectado_em']) / 60:.1f} min) "
                  f"{'resolvido' if r['resolvido'] else 'pendente'}")
    return 0


def cmd_metrics(args) -> int:
    with _store(RAIZ) as st:
        print(json.dumps(st.metricas(args.sprint), indent=1, ensure_ascii=False))
    return 0


def cmd_encerrar(args) -> int:
    """Fecha o sprint. Recusa se houver task fora de estado terminal."""
    from .state import SprintNaoEncerravel
    with _store(RAIZ) as st:
        pend = st.pendentes(args.sprint)
        if pend and not args.forcar:
            print(f"NAO ENCERRAVEL: {len(pend)} task(s) fora de estado terminal:")
            for t in pend:
                print(f"  - {t}")
            print("\nEncerrar com trabalho em aberto e o que o HAQ existe para"
                  " evitar.\nUse --forcar somente para abortar um sprint travado.")
            return 1
        try:
            st.encerrar_sprint(args.sprint, resultado=args.resultado,
                               resumo=" ".join(args.resumo or []),
                               forcar=args.forcar)
        except SprintNaoEncerravel as e:
            print(f"NAO ENCERRADO: {e}")
            return 1
        marca = " (FORCADO)" if args.forcar else ""
        print(f"sprint {args.sprint}: ENCERRADO{marca} — resultado: {args.resultado}")
        if pend:
            print(f"  atencao: {len(pend)} task(s) ficaram em aberto:"
                  f" {', '.join(pend)}")
    return 0


def cmd_evidenciar(args) -> int:
    """Conclui uma task a partir de evidencia JA existente (rota b).

    Para trabalho feito fora do laco do orquestrador. A tentativa fica marcada
    com origem='retroativo' para nao ser confundida com execucao real.
    """
    with _store(RAIZ) as st:
        try:
            att = st.concluir_task_evidenciada(
                args.sprint, args.task, evidencia=args.evidencia,
                test_result={"evidencia": args.evidencia,
                             "comando": args.comando or ""},
                commit=args.commit or "")
        except KeyError as e:
            # str(KeyError) vem com aspas: 'task inexistente: S/T99'
            print(f"erro: {e.args[0] if e.args else e}")
            return 1
        except ValueError as e:
            print(f"erro: {e}")
            return 1
        print(f"{args.task}: DONE  (tentativa {att}, origem retroativo)")
        print(f"  evidencia: {args.evidencia}")
        if args.comando:
            print(f"  comando  : {args.comando}")
    return 0


def cmd_desbloquear(args) -> int:
    """Reabre tasks bloqueadas para uma nova rodada, no degrau pedido.

    Não executa nada: devolve o sprint a EM_EXECUCAO e as tasks a QUEUED, com o
    contador de tentativas no valor pedido — é o contador que escolhe o modelo
    (escalonamento). `--tentativas 2` faz a próxima tentativa ser a 3ª da escada.
    """
    from .config import Config
    cfg = Config.carregar()
    maximo = cfg.policies["retry"]["max_tentativas_implementacao"]
    if args.tentativas >= maximo:
        print(f"RECUSADO: --tentativas {args.tentativas} >= maximo {maximo} —"
              " a task bloquearia de novo na primeira checagem.")
        return 1
    with _store(RAIZ) as st:
        tasks = st.tasks(args.sprint)
        alvos = args.tasks or [t["task_id"] for t in tasks if t["estado"] == "BLOCKED"]
        if not alvos:
            print("nenhuma task bloqueada para reabrir")
            return 0
        de = st.reabrir_sprint(args.sprint,
                               motivo=f"desbloqueio para nova rodada: {', '.join(alvos)}")
        feitas = st.reabrir_tasks(args.sprint, alvos, tentativas=args.tentativas,
                                  motivo=args.motivo or "desbloqueio manual")
        prox = cfg.modelo_para_tentativa(args.tentativas + 1)
        revisor = cfg.revisor_para_tentativa(args.tentativas + 1)
        print(f"sprint {args.sprint}: {de or '(sem estado)'} -> EM_EXECUCAO")
        print(f"  tasks reabertas ({len(feitas)}): {', '.join(feitas)}")
        print(f"  contador de tentativas: {args.tentativas}/{maximo}"
              f"  =>  proxima tentativa e a {args.tentativas + 1}a da escada")
        print(f"  modelo da proxima tentativa: {prox.get('slug')}/{prox.get('effort')}")
        print(f"  revisor da proxima tentativa: {revisor.get('agente') or '-'}"
              f"{' (' + revisor['modelo'] + ')' if revisor.get('modelo') else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="autodev", description="DEVFACTORY orchestrator")
    p.add_argument("--sprint", default=SPRINT_PADRAO)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("detect").set_defaults(fn=cmd_detect)
    sub.add_parser("init").set_defaults(fn=cmd_init)

    s = sub.add_parser("status")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("run")
    s.add_argument("--parar-em", dest="parar_em", default=None)
    s.add_argument("--rodadas", type=int, default=5,
                   help="passadas do laco; cada passada rearma as tasks bloqueadas "
                        "por dependencia ja integrada e para quando nao ha o que "
                        "rearmar (default: 5)")
    s.add_argument("--modo-teste", action="store_true",
                   help="usa o delay curto de cota (aceitacao); producao e 5h10m")
    s.add_argument("--deadline", type=float, default=None,
                   help="segundos de teto de execucao")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("resume")
    s.add_argument("--modo-teste", action="store_true")
    s.set_defaults(fn=cmd_resume)

    sub.add_parser("report").set_defaults(fn=cmd_report)
    sub.add_parser("haq").set_defaults(fn=cmd_haq)
    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    sub.add_parser("metrics").set_defaults(fn=cmd_metrics)

    s = sub.add_parser("encerrar", help="fecha o sprint (estado terminal)")
    s.add_argument("--resultado", default="CONCLUIDO",
                   choices=["CONCLUIDO", "PARCIAL", "ABORTADO"])
    s.add_argument("--resumo", nargs="*", default=[])
    s.add_argument("--forcar", action="store_true",
                   help="aborta sprint travado; registra o motivo no checkpoint")
    s.set_defaults(fn=cmd_encerrar)

    s = sub.add_parser("evidenciar",
                       help="conclui uma task a partir de evidencia existente")
    s.add_argument("task")
    s.add_argument("-e", "--evidencia", required=True)
    s.add_argument("-c", "--comando", default="")
    s.add_argument("--commit", default="")
    s.set_defaults(fn=cmd_evidenciar)

    s = sub.add_parser("desbloquear",
                       help="reabre tasks bloqueadas para uma nova rodada")
    s.add_argument("tasks", nargs="*",
                   help="task ids (padrao: todas as BLOCKED do sprint)")
    s.add_argument("--tentativas", type=int, default=0,
                   help="contador inicial: 2 => a proxima tentativa e a 3a da escada")
    s.add_argument("--motivo", default="")
    s.set_defaults(fn=cmd_desbloquear)

    s = sub.add_parser("stop")
    s.add_argument("nivel", choices=["STOP_ALL", "STOP_PROJECT", "STOP_SPRINT",
                                     "STOP_TASK"])
    s.add_argument("alvo", nargs="?", default="")
    s.add_argument("motivo", nargs="*")
    s.set_defaults(fn=cmd_stop)

    sub.add_parser("start").set_defaults(fn=cmd_start)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
