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
    with pytest.raises(PlanoInvalido):
        parsear_plano("O agente não devolveu um plano.")


def test_recusa_json_sem_chave_tasks():
    resposta = json.dumps({chave: valor for chave, valor in PLANO.items() if chave != "tasks"})

    with pytest.raises(PlanoInvalido):
        parsear_plano(resposta)
