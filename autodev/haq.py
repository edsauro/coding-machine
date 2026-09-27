"""DEVFACTORY — Fila de Ação Humana / HAQ (T11, spec §19).

Uma exigência privilegiada é um item de HAQ, NÃO um motivo para parar o Sprint.
Só as tasks dependentes ficam BLOCKED; as independentes continuam.

Idempotente: o mesmo haq_id não duplica (spec §22).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import errors

CABECALHO = """# HAQ — Human Action Queue · {sprint}

> Itens que exigem humano/privilegio. Cada um lista o comando exato a executar e
> como o Hermes verifica a conclusao. Enquanto isto esta aberto, **somente as
> tasks dependentes ficam bloqueadas** — o resto do Sprint continua.

"""

MODELO = """## {haq_id}
- **Task:** {task_id}
- **Reason:** {reason}
- **Risk:** {risk}
- **Dependency:** {dependencia}
- **Exact human action:**
  ```bash
  {acao_humana}
  ```
- **Expected result:** {resultado}
- **How Hermes verifies completion:**
  ```bash
  {verificacao}
  ```
- **Status:** {status}

"""


@dataclass
class ItemHAQ:
    haq_id: str
    task_id: str
    reason: str
    risk: str
    dependencia: str
    acao_humana: str
    resultado: str
    verificacao: str
    status: str = "OPEN"


def proximo_id(store, sprint: str) -> str:
    existentes = [r["haq_id"] for r in store.haq_listar(sprint)]
    n = 1
    while f"HAQ-{n:03d}" in existentes:
        n += 1
    return f"HAQ-{n:03d}"


def abrir(store, sprint: str, *, task_id: str, reason: str, risk: str,
          dependencia: str, acao_humana: str, resultado: str,
          verificacao: str, failure_class: str = "") -> tuple[str, bool]:
    """Registra um item de HAQ. Devolve (haq_id, foi_novo)."""
    haq_id = proximo_id(store, sprint)
    novo = store.haq_adicionar(
        haq_id, sprint_id=sprint, task_id=task_id, reason=reason, risk=risk,
        dependencia=dependencia, acao_humana=acao_humana, resultado=resultado,
        verificacao=verificacao, failure_class=failure_class)
    return haq_id, novo


def abrir_por_falha(store, sprint: str, *, task_id: str,
                    failure_class: str, detalhe: str, cfg) -> tuple[str, bool]:
    """Converte uma falha humana em item de HAQ com o comando exato."""
    fc = errors.FailureClass(failure_class)
    modelos = {
        errors.FailureClass.PERMISSION_REQUIRED: dict(
            reason="A task precisa de permissao que o agente nao tem (sandbox nega).",
            risk="medio", acao_humana="# autorizar o caminho e repetir a task",
            verificacao="# test -r '<caminho>' && echo OK"),
        errors.FailureClass.SECRET_REQUIRED: dict(
            reason="A task precisa de credencial que nao esta disponivel no sandbox.",
            risk="alto", acao_humana="# exportar a credencial e repetir a task",
            verificacao="# env | grep -q <VAR> && echo OK"),
        errors.FailureClass.RED_ACTION_REQUIRED: dict(
            reason="A task exige acao destrutiva/externa que exige aprovacao humana.",
            risk="alto", acao_humana="# revisar o diff e aprovar manualmente",
            verificacao="# git log --oneline -1"),
    }
    m = modelos.get(fc, dict(reason=f"Falha classificada como {fc.value}.",
                             risk="medio", acao_humana="# investigar",
                             verificacao="# echo OK"))
    return abrir(store, sprint, task_id=task_id or "(sprint)",
                 failure_class=failure_class,
                 reason=m["reason"] + f"\n\nDetalhe tecnico: {detalhe[:500]}",
                 risk=m["risk"],
                 dependencia=f"task {task_id}" if task_id else "sprint",
                 acao_humana=m["acao_humana"], resultado=m["resultado"]
                 if "resultado" in m else "Task deixa de estar bloqueada.",
                 verificacao=m["verificacao"])


def render(store, sprint: str) -> str:
    """Gera o HAQ.md a partir do banco. O banco é a fonte, o .md é a view."""
    itens = store.haq_listar(sprint)
    if not itens:
        return CABECALHO.format(sprint=sprint) + \
            "_Nenhum item. Nenhuma ação humana foi necessária até aqui._\n"
    partes = [CABECALHO.format(sprint=sprint)]
    for r in itens:
        partes.append(MODELO.format(
            haq_id=r["haq_id"], task_id=r["task_id"] or "(sprint)",
            reason=r["reason"], risk=r["risk"], dependencia=r["dependencia"],
            acao_humana=r["acao_humana"], resultado=r["resultado"],
            verificacao=r["verificacao"], status=r["status"]))
    return "".join(partes)


def escrever(store, sprint: str, caminho: str | Path) -> Path:
    p = Path(caminho)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render(store, sprint), encoding="utf-8")
    return p
