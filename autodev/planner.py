"""Tipos e validação do plano de execução de uma sprint."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class TaskPlano:
    id: str
    titulo: str
    criterios: list[str]
    deps: list[str] = field(default_factory=list)
    agente: str = "codex"
    teste: str = ""


@dataclass
class Plano:
    titulo: str
    objetivo: str
    repositorio: str
    tasks: list[TaskPlano]
    prompt_original: str


class PlanoInvalido(ValueError):
    """Indica que a resposta do agente não contém um plano reconhecível."""


def parsear_plano(texto: str) -> Plano:
    """Converte o primeiro objeto JSON com tasks encontrado em um Plano."""
    decoder = json.JSONDecoder()
    objetos: list[dict] = []

    for inicio, caractere in enumerate(texto):
        if caractere != "{":
            continue
        try:
            valor, _ = decoder.raw_decode(texto[inicio:])
        except json.JSONDecodeError:
            continue
        if isinstance(valor, dict):
            objetos.append(valor)

    if not objetos:
        raise PlanoInvalido("resposta sem JSON reconhecível")

    dados = next((objeto for objeto in objetos if "tasks" in objeto), None)
    if dados is None:
        raise PlanoInvalido("JSON sem a chave tasks")

    try:
        tasks = [TaskPlano(**task) for task in dados["tasks"]]
        campos = {**dados, "tasks": tasks}
        return Plano(**campos)
    except (TypeError, KeyError) as erro:
        raise PlanoInvalido(f"estrutura do plano inválida: {erro}") from erro


def validar_plano(plano: Plano) -> list[str]:
    """Retorna todos os problemas estruturais encontrados no plano."""
    erros: list[str] = []
    tasks = plano.tasks
    if not tasks:
        return ["plano sem tasks"]

    ids = [task.id for task in tasks]
    conhecidos = set(ids)
    if len(ids) != len(conhecidos):
        erros.append("ids duplicados no plano")

    for task in tasks:
        if not task.criterios:
            erros.append(f"task {task.id} sem criterios")
        for dep in task.deps:
            if dep not in conhecidos:
                erros.append(f"{task.id} depende de {dep}, que não existe no plano")

    grafo = {task.id: task.deps for task in tasks}
    estado: dict[str, int] = {task_id: 0 for task_id in grafo}

    def visita(task_id: str) -> None:
        estado[task_id] = 1
        for dep in grafo[task_id]:
            if estado.get(dep) == 1:
                erros.append(f"ciclo entre dependências envolvendo {task_id} e {dep}")
            elif estado.get(dep) == 0:
                visita(dep)
        estado[task_id] = 2

    for task_id in grafo:
        if estado[task_id] == 0:
            visita(task_id)
    return erros
