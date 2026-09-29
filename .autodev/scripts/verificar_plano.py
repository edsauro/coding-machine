#!/usr/bin/env python3
"""Verificador de plano (somente leitura) — o lint que o DAG da sprint 2 nao teve.

A sprint 2 fracassou a primeira noite por um defeito de PLANO, nao de modelo:
8 das 10 tasks gravavam os testes no mesmo arquivo e 6 editavam o mesmo modulo.
Cada task que obedecia aos proprios criterios apagava o trabalho da anterior; o
revisor reprovava por regressao; a task queimava as 5 tentativas e bloqueava.
(D-16 em .autodev/sprints/DEVFACTORY-002/decisions.md.)

Uso:
    python3 .autodev/scripts/verificar_plano.py DEVFACTORY-003
    python3 .autodev/scripts/verificar_plano.py .autodev/sprints/X/dag.json
    python3 .autodev/scripts/verificar_plano.py --todos

Regras (ERRO bloqueia; AVISO exige justificativa no plano):
  E1 ids duplicados, dep inexistente, ciclo            (delega para config.valida_dag)
  E2 duas tasks da MESMA onda citam o mesmo arquivo
  E3 duas tasks citam o MESMO arquivo de teste (qualquer onda)  <- a armadilha D-16
  E4 task sem nenhum arquivo nomeado nos criterios (criterio nao verificavel)
  E5 invocacao pytest deve usar `python3 -m pytest ...`; teste sem venv relativo
  A1 arquivo compartilhado entre ondas diferentes (exige criterio de preservacao)
  A2 task que mexe em modulo de outra task sem depender dela

Saida: 0 se nenhum ERRO; 1 se houver ERRO.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from autodev import planner

ARQUIVO = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|md|json|ya?ml|toml|sh|txt|cfg|ini)")
IGNORAR = (".venv/", "site-packages/")
COMANDO_PYTEST_CORRETO = re.compile(r"(?<![A-Za-z0-9_./-])python3\s+-m\s+pytest\b")
CAMINHO_VENV_RELATIVO = re.compile(r"(?<![A-Za-z0-9_./-])(?:\./)?\.?venv/bin/(?:python(?:3)?|pytest)\b")
NEGACOES_COMANDO = re.compile(r"\b(?:nao\s+(?:use|usar|rode|rodar)|não\s+(?:use|usar|rode|rodar)|proibid[oa]|nunca|em\s+vez\s+de)\b", re.I)

# Verbo de edicao: sem ele, a citacao do caminho e MENCAO (import, exemplo,
# arquivo que a task apenas le), nao entrega. Sem esta distincao o verificador
# acusa falso positivo — "importada de autodev/planner.py" nao edita planner.py.
VERBOS_EDICAO = (
    "existe", "criar", "cria", "crie", "estende", "estender", "edita", "editar",
    "ganha", "atualiza", "adiciona", "remove", "reescreve", "escreve", "implementa",
    "altera", "substitui", "move", "grava", "ficam em", "fica em", "passa a",
    "novo arquivo", "novo modulo", "registra em",
)
# Casamento por PALAVRA, nao por substring: sem isso "reimplementar" casaria com
# "implementa" e um criterio que so cita o modulo alheio viraria "edita".
_PADRAO_EDICAO = re.compile(r"\b(?:" + "|".join(re.escape(v) for v in VERBOS_EDICAO) + r")\b")
# Negacao de edicao: "nao cria ciclo de import com X" cita X, nao edita X.
NEGACOES_EDICAO = (
    "nao cria", "nao edita", "nao altera", "nao remove", "nao reescreve",
    "nao toca", "sem editar", "sem alterar", "sem remover", "sem tocar",
)


def tem_verbo_de_edicao(texto: str) -> bool:
    t = texto.lower()
    if any(n in t for n in NEGACOES_EDICAO):
        return False
    return _PADRAO_EDICAO.search(t) is not None


def extrair_arquivos(task: dict) -> set[str]:
    """Caminhos citados nos criterios e no campo teste da task."""
    texto = "\n".join(task.get("criterios", []) + [task.get("teste", "") or ""])
    achados = set()
    for m in ARQUIVO.findall(texto):
        p = m.strip().lstrip("./")
        if any(i in p for i in IGNORAR):
            continue
        achados.add(p)
    return achados


def extrair_tocados(task: dict) -> set[str]:
    """Arquivos que a task EDITA: caminho num criterio com verbo de edicao, ou
    o arquivo de teste declarado no campo `teste`."""
    tocados = {m.strip().lstrip("./")
               for m in ARQUIVO.findall(task.get("teste", "") or "")
               if not any(i in m for i in IGNORAR)}
    for c in task.get("criterios", []):
        if not tem_verbo_de_edicao(c):
            continue
        for m in ARQUIVO.findall(c):
            p = m.strip().lstrip("./")
            if not any(i in p for i in IGNORAR):
                tocados.add(p)
    return tocados


def _sentencas(texto: str) -> list[str]:
    """Separa instrucoes para nao confundir uma proibicao com um comando."""
    return [
        trecho.strip()
        for trecho in re.split(r"[;\n]+|\.(?=\s|$)", texto)
        if trecho.strip()
    ]


def _comando_de_teste_incorreto(texto: str, *, campo_teste: bool) -> bool:
    """Reconhece comandos pytest afirmativos fora da forma do runner."""
    for trecho in _sentencas(texto):
        if NEGACOES_COMANDO.search(trecho):
            continue
        contexto_teste = campo_teste or re.search(
            r"-m\s+pytest\b|\btest\w*|\bsu[ií]te\b|\bpytest\s+-", trecho, re.I,
        )
        if CAMINHO_VENV_RELATIVO.search(trecho) and contexto_teste:
            return True
        # Retire apenas a invocacao correta, nao a frase inteira: um segundo
        # comando incorreto na mesma frase ainda precisa ser diagnosticado.
        restante = COMANDO_PYTEST_CORRETO.sub("", trecho)
        if re.search(r"\bpython[\d.]*\s+-m\s+pytest\b", restante):
            return True
        if re.search(
            r"\bpytest\b(?=\s+(?:-|[\w./-]*[/\\]|test[\w.-]*\b))"
            r"|(?:^|[&|`>$])\s*pytest\s*(?:$|[`;&|])"
            r"|\b(?:rode|rodar|execute|executar)\s+pytest\b",
            restante, re.I,
        ):
            return True
    return False


def ondas_lista(dag: dict) -> list[list[str]]:
    """Ondas de execucao — usa o motor real quando a sprint ja e conhecida."""
    try:
        from autodev.config import ordem_topologica
        return [list(o) for o in ordem_topologica(dag)]
    except Exception:
        pass
    deps = {t["id"]: set(t.get("deps", [])) for t in dag.get("tasks", [])}
    feitas: list[str] = []
    saida: list[list[str]] = []
    while len(feitas) < len(deps):
        onda = sorted(i for i, d in deps.items()
                      if i not in feitas and d.issubset(set(feitas)))
        if not onda:
            saida.append(sorted(set(deps) - set(feitas)))
            break
        feitas += onda
        saida.append(onda)
    return saida


def verificar(dag: dict, nome: str) -> tuple[list[str], list[str]]:
    erros: list[str] = []
    avisos: list[str] = []
    tasks = dag.get("tasks", [])
    if not tasks:
        return ["E4: plano sem tasks"], avisos

    try:
        from autodev.config import valida_dag
        erros += [f"E1: {e}" for e in valida_dag(dag)]
    except Exception as exc:  # pragma: no cover
        avisos.append(f"nao foi possivel rodar valida_dag do motor ({exc}); seguindo com a checagem local")

    arquivos = {t["id"]: extrair_arquivos(t) for t in tasks}      # mencoes (E4)
    tocados = {t["id"]: extrair_tocados(t) for t in tasks}        # edicoes (A2)
    plano = planner.Plano(
        titulo=nome,
        objetivo="",
        repositorio=".",
        prompt_original="",
        tasks=[
            planner.TaskPlano(
                id=t["id"],
                titulo=t.get("titulo", t["id"]),
                criterios=list(t.get("criterios", [])),
                deps=list(t.get("deps", [])),
                agente=t.get("agente", "codex"),
                teste=t.get("teste", "") or "",
            )
            for t in tasks
        ],
    )

    # E5 — o runner resolve `python3` para o interpretador do projeto. Um
    # caminho de venv relativo aponta para o worktree, onde esse venv não existe.
    for t in tasks:
        teste = t.get("teste", "") or ""
        criterio_incorreto = any(
            _comando_de_teste_incorreto(c, campo_teste=False)
            for c in t.get("criterios", [])
        )
        if _comando_de_teste_incorreto(teste, campo_teste=True) or criterio_incorreto:
            erros.append(
                f"E5: {t['id']} usa comando de teste fora da forma exigida; "
                "use python3 -m pytest ..."
                + ("; o worktree nao tem venv" if any(
                    CAMINHO_VENV_RELATIVO.search(trecho)
                    and _comando_de_teste_incorreto(trecho, campo_teste=campo)
                    for texto, campo in [(teste, True)] + [
                        (c, False) for c in t.get("criterios", [])
                    ]
                    for trecho in _sentencas(texto)
                ) else "")
            )

    onda_de: dict[str, int] = {}
    for n, onda in enumerate(ondas_lista(dag)):
        for i in onda:
            onda_de[i] = n

    # E2/E3 — a implementação canônica fica no planner usado pelo motor.
    ids = [t["id"] for t in tasks]
    for colisao in planner.colisoes_de_arquivo(plano):
        ia = str(colisao["task_a"])
        ib = str(colisao["task_b"])
        arq = str(colisao["arquivo"])
        if planner._arquivo_de_teste(arq):
            erros.append(f"E3: {ia} e {ib} citam o MESMO arquivo de teste {arq} "
                         f"— cada task precisa do seu (armadilha D-16)")
        else:
            erros.append(f"E2: {ia} e {ib} estao na MESMA onda {colisao['onda']} "
                         f"e citam {arq} — serialize (adicione deps) ou separe o arquivo")

    # E4 + A2
    def alcanca(origem: str, alvo: str, grafo: dict[str, list[str]],
                vistos: set[str] | None = None) -> bool:
        """origem depende de alvo, direta ou transitivamente."""
        vistos = vistos or set()
        if origem in vistos:
            return False
        vistos.add(origem)
        for d in grafo.get(origem, []):
            if d == alvo or alcanca(d, alvo, grafo, vistos):
                return True
        return False

    grafo = {t["id"]: list(t.get("deps", [])) for t in tasks}
    for t in tasks:
        tid = t["id"]
        if not arquivos[tid]:
            erros.append(f"E4: {tid} nao cita nenhum arquivo concreto nos criterios "
                         f"— criterio nao verificavel")
        for outro in ids:
            if outro == tid or not (tocados[tid] & tocados[outro]):
                continue
            # so modulos de codigo entram aqui: editar o dag.json/spec.md do
            # proprio sprint nao e "estender modulo de outra task"
            if not any(a.endswith(".py") for a in tocados[tid] & tocados[outro]):
                continue
            # se o outro entrega ANTES e esta task mexe no mesmo arquivo, ela
            # deveria depender dele (a menos que ja alcance por outro caminho)
            if onda_de.get(outro, -1) < onda_de.get(tid, 0) \
                    and not alcanca(tid, outro, grafo):
                avisos.append(f"A2: {tid} mexe em {sorted(tocados[tid] & tocados[outro])} "
                              f"que {outro} entrega antes, mas nao depende de {outro}")

    # A1 — arquivo compartilhado em ondas diferentes: exige preservacao explicita.
    tarefas_por_id = {t["id"]: t for t in tasks}
    for aviso in planner.avisos_de_colisao_de_arquivo(plano):
        primeiro = str(aviso["task_a"])
        posterior = str(aviso["task_b"])
        arq = str(aviso["arquivo"])
        txt = " ".join(tarefas_por_id[posterior].get("criterios", [])).lower()
        if "preserv" not in txt and "estend" not in txt and "nao remover" not in txt:
            avisos.append(f"A1: {posterior} cita {arq} (citado por {primeiro}) sem "
                          f"criterio explicito de preservacao/estender")

    return erros, avisos


def carregar(alvo: str) -> tuple[dict, str]:
    p = Path(alvo)
    if not p.exists():
        p = RAIZ / ".autodev" / "sprints" / alvo / "dag.json"
    if not p.exists():
        raise SystemExit(f"nao encontrei {alvo} nem .autodev/sprints/{alvo}/dag.json")
    return json.loads(p.read_text(encoding="utf-8")), p.parent.name


def main() -> int:
    ap = argparse.ArgumentParser(description="Verifica um plano de sprint (somente leitura)")
    ap.add_argument("alvo", nargs="*", help="sprint_id ou caminho do dag.json")
    ap.add_argument("--todos", action="store_true", help="verifica todas as sprints")
    args = ap.parse_args()

    if args.todos:
        alvos = sorted((RAIZ / ".autodev" / "sprints").glob("*/dag.json"))
    elif args.alvo:
        alvos = list(args.alvo)
    else:
        ap.error("informe a sprint, o dag.json ou --todos")

    total_erros = 0
    for a in alvos:
        dag, nome = carregar(str(a))
        erros, avisos = verificar(dag, nome)
        total_erros += len(erros)
        marca = "OK   " if not erros else "ERRO "
        print(f"{marca} {nome}: {len(dag.get('tasks', []))} tasks, "
              f"{len(erros)} erro(s), {len(avisos)} aviso(s)")
        for e in erros:
            print(f"       x {e}")
        for w in avisos:
            print(f"       ! {w}")
    return 1 if total_erros else 0


if __name__ == "__main__":
    raise SystemExit(main())
