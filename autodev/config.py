"""DEVFACTORY — carregamento de configuração e schemas do Sprint (T02).

Os schemas ficam aqui, em código, e são validados na carga: um sprint.yaml ou
dag.json malformado deve falhar cedo e alto, não no meio de uma execução.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
AUTODEV = RAIZ / ".autodev"
CONFIG = AUTODEV / "config"

ESTADOS = ["NEW", "PLANNED", "QUEUED", "RUNNING", "VERIFYING", "BLOCKED",
           "REVIEW", "RETRY", "DONE", "FAILED", "INTEGRATED", "WAITING_RESOURCE"]

TRANSICOES = {
    "NEW": {"PLANNED", "BLOCKED"},
    "PLANNED": {"QUEUED", "BLOCKED"},
    "QUEUED": {"RUNNING", "BLOCKED", "WAITING_RESOURCE"},
    "RUNNING": {"VERIFYING", "RETRY", "FAILED", "BLOCKED", "WAITING_RESOURCE"},
    "VERIFYING": {"REVIEW", "RETRY", "DONE", "FAILED", "BLOCKED"},
    "REVIEW": {"DONE", "RETRY", "FAILED", "BLOCKED"},
    "RETRY": {"QUEUED", "BLOCKED", "FAILED", "WAITING_RESOURCE"},
    "WAITING_RESOURCE": {"QUEUED", "RUNNING", "BLOCKED"},
    "DONE": {"INTEGRATED"},
    "INTEGRATED": set(),
    "FAILED": {"QUEUED"},
    "BLOCKED": {"QUEUED"},
}


def _carrega_yaml(p: Path) -> dict:
    if not p.exists():
        raise FileNotFoundError(f"config ausente: {p}")
    with p.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass
class Config:
    agents: dict
    policies: dict
    models: dict
    raiz: Path = RAIZ

    @classmethod
    def carregar(cls, config_dir: Path | None = None) -> "Config":
        d = config_dir or CONFIG
        return cls(agents=_carrega_yaml(d / "agents.yaml"),
                   policies=_carrega_yaml(d / "policies.yaml"),
                   models=_carrega_yaml(d / "models.yaml"))

    # -- helpers de política -------------------------------------------------
    def escada(self) -> list[dict]:
        return self.models["codex"]["ladder"]

    def modelo_para_tentativa(self, tentativa: int) -> dict:
        """Tentativa N -> degrau da escada (spec §10). 1-indexado."""
        mapa = self.policies["retry"]["escalonamento"]
        tier = mapa.get(min(tentativa, max(mapa)), max(mapa.values()))
        degraus = [d for d in self.escada() if d["tier"] == tier]
        return degraus[0] if degraus else self.models["codex"]["default"]

    def classes_sem_escalonamento(self) -> set[str]:
        return set(self.policies["retry"]["classes_sem_escalonamento"])

    def espera_cota(self, modo_teste: bool = False) -> int:
        c = self.policies["cota_codex"]
        return c["espera_teste_segundos"] if modo_teste else c["espera_padrao_segundos"]

    def permissoes(self) -> dict:
        return self.policies["seguranca"]["permissoes"]

    def branch_integracao(self, sprint: str) -> str:
        """Branch de integração do sprint.

        A política guarda um TEMPLATE (`sprint/{sprint}/integration`); usar o
        valor cru faria todo sprint integrar no mesmo branch.
        """
        bruto = str(self.policies["integracao"].get(
            "branch_sprint", "sprint/{sprint}/integration"))
        return bruto.format(sprint=sprint)

    def caminhos_proibidos(self) -> list[str]:
        return self.policies["seguranca"]["caminhos_proibidos"]


# --- schema do sprint --------------------------------------------------------

@dataclass
class Task:
    id: str
    titulo: str
    criterios: list[str]
    deps: list[str] = field(default_factory=list)
    agente: str = "codex"
    paralelizavel: bool = False
    estimativa: str = ""

    @staticmethod
    def validar(d: dict) -> None:
        faltando = {"id", "titulo", "criterios"} - set(d)
        if faltando:
            raise ValueError(f"task sem campos obrigatórios {faltando}: {d}")
        if not isinstance(d["criterios"], list) or not d["criterios"]:
            raise ValueError(f"task {d['id']}: criterios precisa ser lista não vazia")


def valida_dag(dag: dict) -> list[str]:
    """Valida o DAG e devolve os erros encontrados (lista vazia = ok).

    Checa: ids únicos, deps existem, sem ciclos, e que nenhuma task
    paralelizável dependa de outra do mesmo grupo paralelo sem ser marcada.
    """
    erros: list[str] = []
    tasks = dag.get("tasks", [])
    ids = [t.get("id") for t in tasks]
    if len(ids) != len(set(ids)):
        erros.append("ids duplicados no DAG")
    conhecidos = set(ids)
    for t in tasks:
        try:
            Task.validar(t)
        except ValueError as e:
            erros.append(str(e))
        for d in t.get("deps", []):
            if d not in conhecidos:
                erros.append(f"{t['id']} depende de {d}, que não existe no DAG")
    # detecção de ciclo (DFS com marcação de cor)
    grafo = {t["id"]: list(t.get("deps", [])) for t in tasks}
    BRANCO, CINZA, PRETO = 0, 1, 2
    cor = {i: BRANCO for i in grafo}

    def visita(n: str, pilha: list[str]) -> None:
        cor[n] = CINZA
        for d in grafo.get(n, []):
            if cor.get(d) == CINZA:
                erros.append(f"ciclo: {' -> '.join(pilha + [n, d])}")
            elif cor.get(d) == BRANCO:
                visita(d, pilha + [n])
        cor[n] = PRETO

    for n in grafo:
        if cor[n] == BRANCO:
            visita(n, [])
    return erros


def carrega_dag(p: Path) -> dict:
    with p.open(encoding="utf-8") as fh:
        dag = json.load(fh)
    erros = valida_dag(dag)
    if erros:
        raise ValueError("DAG inválido:\n  - " + "\n  - ".join(erros))
    return dag


def carrega_sprint(p: Path) -> dict:
    with p.open(encoding="utf-8") as fh:
        s = yaml.safe_load(fh) or {}
    for campo in ("sprint_id", "objetivo"):
        if campo not in s:
            raise ValueError(f"sprint.yaml sem '{campo}': {p}")
    return s


def ordem_topologica(dag: dict) -> list[list[str]]:
    """Devolve as 'ondas' do DAG: cada onda pode rodar em paralelo."""
    grafo = {t["id"]: set(t.get("deps", [])) for t in dag["tasks"]}
    ondas, restante = [], dict(grafo)
    while restante:
        prontos = sorted(n for n, d in restante.items() if not (d & set(restante)))
        if not prontos:
            raise ValueError("DAG com ciclo — nenhuma onda progressa")
        ondas.append(prontos)
        for n in prontos:
            del restante[n]
    return ondas
