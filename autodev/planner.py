"""Tipos e validação do plano de execução de uma sprint."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from . import agents, config
from .plan_prompt import montar_prompt_plano


PALAVRAS_VAGAS = ["melhorar", "otimizar", "refatorar", "revisar", "ajustar"]

# Referências textuais: o arquivo pode ainda ser criado pela sprint.
_ARQUIVO = re.compile(
    r"(?<![\w.])(?:[\w.-]+/)*[\w-]+(?:\.[\w-]+)*\.[A-Za-z][A-Za-z0-9]*\b"
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


class CotaEsgotada(RuntimeError):
    """Indica que o agente não pôde planejar por falta de cota."""


def planejar(raiz: str | Path, prompt_usuario: str, agente: str) -> Plano:
    """Solicita um plano somente-leitura ao agente e converte sua resposta."""
    raiz = Path(raiz)
    sprints = raiz / ".autodev" / "sprints"
    candidatos = sorted(
        (caminho
        for caminho in sprints.glob("DEVFACTORY-[0-9]*")
        if caminho.is_dir() and re.fullmatch(r"DEVFACTORY-\d+", caminho.name)),
        key=lambda caminho: int(caminho.name.split("-")[-1]),
    )
    if not candidatos:
        raise PlanoInvalido("nenhum sprint encontrado para registrar o planejamento")

    prompt = montar_prompt_plano(prompt_usuario)
    log_path = candidatos[-1] / "logs" / f"plano-{agente}.log"
    historico = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    resultado = agents.invocar(
        agents.Invocacao(
            agente=agente,
            prompt=prompt,
            worktree=str(raiz),
            edita=False,
            timeout=int(os.environ.get("AUTODEV_AGENT_TIMEOUT", "900")),
            log_path=str(log_path),
            fake_script=os.environ.get("AUTODEV_FAKE_SPEC") or None,
        ),
        config.Config.carregar(),
    )
    if historico:
        # O adaptador sobrescreve o log; preserve as invocações anteriores.
        atual = log_path.read_text(encoding="utf-8")
        log_path.write_text(historico + "\n" + atual, encoding="utf-8")

    if resultado.failure_class == "CODEX_QUOTA":
        raise CotaEsgotada("cota do Codex esgotada durante o planejamento")
    if not resultado.ok:
        raise RuntimeError(
            f"falha do agente: {resultado.failure_class}; "
            f"exit_code={resultado.exit_code}; {resultado.stderr or resultado.stdout}"
        )

    resposta = resultado.stdout or resultado.stderr
    try:
        plano = parsear_plano(resposta)
    except PlanoInvalido as erro:
        trecho = resposta[:160].replace("\n", " ")
        raise PlanoInvalido(f"{erro}; início da resposta: {trecho!r}") from erro
    plano.prompt_original = prompt_usuario
    return plano


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
    dag = {"sprint_id": sprint_id, "versao": 1, "tasks": tasks,
           "prompt_original": plano.prompt_original}
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
                for nome in ("titulo", "objetivo", "repositorio")
            }
            campos["prompt_original"] = dados.get("prompt_original", "")
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


def _arquivos_da_task(task: TaskPlano) -> set[str]:
    """Extrai todos os caminhos mencionados nos critérios e no teste."""
    textos = [*task.criterios, task.teste]
    return {
        _normalizar_caminho(caminho.group(0))
        for texto in textos
        for caminho in _ARQUIVO.finditer(texto.replace("\\", "/"))
    }


def _normalizar_caminho(caminho: str) -> str:
    """Normaliza separadores e prefixos relativos sem acessar o filesystem."""
    caminho = caminho.replace("\\", "/")
    while caminho.startswith("./"):
        caminho = caminho[2:]
    return PurePosixPath(caminho).as_posix().lstrip("/")


def arquivo_de_teste(caminho: str) -> bool:
    """Reconhece diretórios de testes e nomes convencionais de testes Python."""
    caminho = PurePosixPath(_normalizar_caminho(caminho))
    partes = {parte.casefold() for parte in caminho.parts[:-1]}
    nome = caminho.name.casefold()
    return bool(
        partes & {"tests", "test"}
        or nome == "conftest.py"
        or (nome.endswith(".py") and (nome.startswith("test_") or nome.endswith("_test.py")))
    )


def _ondas_por_task(plano: Plano) -> dict[str, int] | None:
    """Calcula ondas sem mascarar os erros de IDs duplicados ou ciclos."""
    if len({task.id for task in plano.tasks}) != len(plano.tasks):
        return None
    try:
        ondas = config.ordem_topologica({"tasks": [
            {"id": task.id, "deps": task.deps} for task in plano.tasks
        ]})
    except ValueError:
        return None
    return {
        task_id: indice + 1
        for indice, onda in enumerate(ondas)
        for task_id in onda
    }


def colisoes_de_arquivo(plano: Plano) -> list[dict[str, str | int]]:
    """Retorna arquivos citados por tasks conflitantes no mesmo plano.

    Na mesma onda, qualquer arquivo comum colide. Arquivos de
    teste colidem em qualquer onda para evitar a sobrescrita observada no D-16.
    """
    onda_por_task = _ondas_por_task(plano)
    if onda_por_task is None:
        return []
    arquivos_mencionados = [_arquivos_da_task(task) for task in plano.tasks]
    colisoes: list[dict[str, str | int]] = []
    for indice, task_a in enumerate(plano.tasks):
        for indice_b, task_b in enumerate(plano.tasks[indice + 1:], indice + 1):
            onda_a = onda_por_task[task_a.id]
            onda_b = onda_por_task[task_b.id]
            for arquivo in sorted(
                arquivos_mencionados[indice] & arquivos_mencionados[indice_b]
            ):
                if arquivo_de_teste(arquivo) or onda_a == onda_b:
                    colisoes.append({
                        "task_a": task_a.id,
                        "task_b": task_b.id,
                        "arquivo": arquivo,
                        "onda": onda_a,
                    })
    return colisoes


def avisos_de_colisao_de_arquivo(plano: Plano) -> list[dict[str, str | int]]:
    """Retorna caminhos de produção citados em ondas diferentes como avisos."""
    onda_por_task = _ondas_por_task(plano)
    if onda_por_task is None:
        return []
    arquivos_mencionados = [_arquivos_da_task(task) for task in plano.tasks]
    avisos: list[dict[str, str | int]] = []
    for indice, task_a in enumerate(plano.tasks):
        for indice_b, task_b in enumerate(plano.tasks[indice + 1:], indice + 1):
            onda_a = onda_por_task[task_a.id]
            onda_b = onda_por_task[task_b.id]
            if onda_a == onda_b:
                continue
            for arquivo in sorted(
                arquivos_mencionados[indice] & arquivos_mencionados[indice_b]
            ):
                if not arquivo_de_teste(arquivo):
                    avisos.append({
                        "task_a": task_a.id,
                        "task_b": task_b.id,
                        "arquivo": arquivo,
                        "onda": onda_a,
                        "onda_b": onda_b,
                    })
    return avisos


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
    for colisao in colisoes_de_arquivo(plano):
        erros.append(
            f"colisão de arquivo entre {colisao['task_a']} e {colisao['task_b']}: "
            f"{colisao['arquivo']} (onda {colisao['onda']})"
        )
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
