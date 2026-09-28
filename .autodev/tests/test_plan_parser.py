import json

import pytest

from autodev.planner import Plano, PlanoInvalido, TaskPlano, parsear_plano


PLANO = {
    "titulo": "Sprint de parser",
    "objetivo": "Interpretar a resposta do agente",
    "repositorio": ".",
    "tasks": [
        {
            "id": "T1",
            "titulo": "Implementar parser",
            "criterios": ["aceita JSON"],
            "deps": [],
            "agente": "codex",
            "teste": "python3 -m pytest",
        }
    ],
    "prompt_original": "crie o parser",
}


@pytest.mark.parametrize(
    "resposta",
    [
        json.dumps(PLANO),
        f"```json\n{json.dumps(PLANO)}\n```",
        f"Segue o plano solicitado:\n{json.dumps(PLANO)}\nEspero que ajude.",
    ],
    ids=["json-puro", "cerca-de-codigo", "texto-explicativo"],
)
def test_parseia_formatos_tolerados(resposta):
    plano = parsear_plano(resposta)

    assert plano == Plano(
        titulo="Sprint de parser",
        objetivo="Interpretar a resposta do agente",
        repositorio=".",
        tasks=[
            TaskPlano(
                id="T1",
                titulo="Implementar parser",
                criterios=["aceita JSON"],
                deps=[],
                agente="codex",
                teste="python3 -m pytest",
            )
        ],
        prompt_original="crie o parser",
    )


def test_recusa_texto_sem_json_reconhecivel():
    with pytest.raises(PlanoInvalido, match="reconhecível"):
        parsear_plano("O agente não devolveu um plano.")


def test_recusa_json_sem_chave_tasks():
    resposta = json.dumps({chave: valor for chave, valor in PLANO.items() if chave != "tasks"})

    with pytest.raises(PlanoInvalido, match="tasks"):
        parsear_plano(resposta)


@pytest.mark.parametrize("campo", ["criterios", "deps"])
def test_recusa_lista_nula_em_task(campo):
    dados = json.loads(json.dumps(PLANO))
    dados["tasks"][0][campo] = None

    with pytest.raises(PlanoInvalido, match="estrutura do plano inválida"):
        parsear_plano(json.dumps(dados))


def test_ignora_metadados_extras_do_agente():
    dados = json.loads(json.dumps(PLANO))
    dados["versao"] = 1
    dados["tasks"][0]["estimativa"] = "pequena"

    plano = parsear_plano(json.dumps(dados))

    assert plano.titulo == PLANO["titulo"]
    assert plano.tasks[0].id == "T1"


def test_recusa_resposta_nao_textual_com_erro_do_dominio():
    with pytest.raises(PlanoInvalido, match="reconhecível"):
        parsear_plano(None)
