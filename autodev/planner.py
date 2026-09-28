"""Tipos e validação do plano de execução de uma sprint."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from . import config


PALAVRAS_VAGAS = ["melhorar", "otimizar", "refatorar", "revisar", "ajustar"]

# Referências textuais: o arquivo pode ainda ser criado pela sprint.
_ARQUIVO = re.compile(
    r"(?<![\w.])(?:[\w.-]+/)*[\w-]+\.[A-Za-z][A-Za-z0-9]*\b"
    r"|\b(?:Makefile|Dockerfile)\b"
)
_COMANDO_TESTE = re.compile(
    r"\b(?:pytest|(?:python(?:3)?\s+-m\s+unittest)|"
    r"(?:npm|pnpm|yarn)\s+(?:run\s+)?test|"
    r"(?:go|cargo)\s+test|make\s+test)\b",
    re.IGNORECASE,
)


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


class SprintJaExiste(FileExistsError):
    """Indica que o diretório de destino do sprint já existe."""


def escrever_sprint(raiz: str | Path, plano: Plano) -> Path:
    """Persiste um plano em um novo diretório sequencial de sprint."""
    diretorio_sprints = Path(raiz) / ".autodev" / "sprints"
    diretorio_sprints.mkdir(parents=True, exist_ok=True)

    numeros = [
        int(casamento.group(1))
        for caminho in diretorio_sprints.iterdir()
        if (casamento := re.fullmatch(r"DEVFACTORY-(\d+)", caminho.name))
    ]
    sprint_id = f"DEVFACTORY-{max(numeros, default=0) + 1:03d}"
    destino = diretorio_sprints / sprint_id
    try:
        destino.mkdir()
    except FileExistsError as erro:
        raise SprintJaExiste(f"sprint já existe: {sprint_id}") from erro

    tasks = [asdict(task) for task in plano.tasks]
    dag = {"sprint_id": sprint_id, "versao": 1, "tasks": tasks}
    (destino / "dag.json").write_text(
        json.dumps(dag, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sprint = {
        "sprint_id": sprint_id,
        "titulo": plano.titulo,
        "objetivo": plano.objetivo,
        "status": "PLANEJADO",
        "repositorio": {
            "raiz": plano.repositorio,
            "merge_em_main": False,
        },
    }
    (destino / "sprint.yaml").write_text(
        yaml.safe_dump(sprint, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    spec = (
        f"# Prompt original\n\n{plano.prompt_original}\n\n"
        f"# {plano.titulo}\n\n{plano.objetivo}\n"
    )
    (destino / "spec.md").write_text(spec, encoding="utf-8")
    return destino


def parsear_plano(texto: str) -> Plano:
    """Converte um objeto JSON com ``tasks`` encontrado no texto em ``Plano``."""
    if not isinstance(texto, str):
        raise PlanoInvalido("resposta sem JSON reconhecível")

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

    candidatos = [objeto for objeto in objetos if "tasks" in objeto]
    if not candidatos:
        raise PlanoInvalido("JSON sem a chave tasks")

    ultimo_erro: (TypeError | KeyError | ValueError) | None = None
    for dados in candidatos:
        try:
            tasks_brutas = dados["tasks"]
            if not isinstance(tasks_brutas, list):
                raise TypeError("tasks deve ser uma lista")

            tasks = [_construir_task(task) for task in tasks_brutas]
            campos = {
                nome: dados[nome]
                for nome in ("titulo", "objetivo", "repositorio", "prompt_original")
            }
            for nome, valor in campos.items():
                if not isinstance(valor, str):
                    raise TypeError(f"{nome} deve ser uma string")
            return Plano(**campos, tasks=tasks)
        except (TypeError, KeyError, ValueError) as erro:
            ultimo_erro = erro

    raise PlanoInvalido(f"estrutura do plano inválida: {ultimo_erro}") from ultimo_erro


def _construir_task(dados: object) -> TaskPlano:
    """Valida os tipos de uma task e ignora metadados desconhecidos."""
    if not isinstance(dados, dict):
        raise TypeError("cada task deve ser um objeto")

    campos = {
        nome: dados[nome]
        for nome in ("id", "titulo", "criterios")
    }
    campos["deps"] = dados.get("deps", [])
    campos["agente"] = dados.get("agente", "codex")
    campos["teste"] = dados.get("teste", "")

    for nome in ("id", "titulo", "agente", "teste"):
        if not isinstance(campos[nome], str):
            raise TypeError(f"{nome} deve ser uma string")
    for nome in ("criterios", "deps"):
        valor = campos[nome]
        if not isinstance(valor, list) or not all(isinstance(item, str) for item in valor):
            raise TypeError(f"{nome} deve ser uma lista de strings")

    return TaskPlano(**campos)


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


def validar_e_ordenar(plano: Plano) -> list[list[str]]:
    """Valida o DAG e os critérios e retorna ondas; índices começam em 1."""
    dag = {"tasks": [
        {"id": task.id, "titulo": task.titulo,
         "criterios": task.criterios, "deps": task.deps}
        for task in plano.tasks
    ]}
    erros = config.valida_dag(dag)
    if not plano.tasks:
        erros.append("plano sem tasks")

    for task in plano.tasks:
        for indice, criterio in enumerate(task.criterios, start=1):
            palavras = set(re.findall(r"\w+", criterio.casefold()))
            if (palavras.intersection(PALAVRAS_VAGAS)
                    and not _ARQUIVO.search(criterio)
                    and not _COMANDO_TESTE.search(criterio)):
                erros.append(
                    f"task {task.id}: critério {indice} vago; "
                    "cite um arquivo ou comando de teste"
                )

    if erros:
        raise PlanoInvalido("Plano inválido:\n  - " + "\n  - ".join(erros))
    return config.ordem_topologica(dag)
