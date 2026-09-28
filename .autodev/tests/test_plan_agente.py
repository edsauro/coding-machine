import json

import pytest

from autodev.planner import CotaEsgotada, PlanoInvalido, planejar


def _preparar_sprint(raiz, sprint_id="DEVFACTORY-007"):
    logs = raiz / ".autodev" / "sprints" / sprint_id / "logs"
    logs.mkdir(parents=True)
    return logs


def _configurar_fake(monkeypatch, tmp_path, resposta):
    spec = tmp_path / "agente.json"
    spec.write_text(
        json.dumps({"acao": "echo", "texto": resposta}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(spec))


def _resposta_valida():
    return json.dumps(
        {
            "titulo": "API planejada",
            "objetivo": "Entregar uma API testada",
            "repositorio": ".",
            "tasks": [
                {
                    "id": "P01",
                    "titulo": "Implementar API",
                    "criterios": ["python3 -m pytest tests/test_api.py -q passa"],
                    "deps": [],
                    "agente": "codex",
                    "teste": "python3 -m pytest tests/test_api.py -q",
                }
            ],
        },
        ensure_ascii=False,
    )


def test_planejar_devolve_plano_usando_driver_falso(tmp_path, monkeypatch):
    _preparar_sprint(tmp_path)
    _configurar_fake(monkeypatch, tmp_path, _resposta_valida())

    plano = planejar(tmp_path, "Planeje uma API", "codex")

    assert plano.titulo == "API planejada"
    assert plano.tasks[0].id == "P01"


def test_planejar_grava_prompt_e_resposta_no_log_do_sprint(tmp_path, monkeypatch):
    logs = _preparar_sprint(tmp_path, "DEVFACTORY-012")
    resposta = _resposta_valida()
    _configurar_fake(monkeypatch, tmp_path, resposta)

    planejar(tmp_path, "Pedido auditável", "agy")

    auditoria = (logs / "plano-agy.log").read_text(encoding="utf-8")
    assert "Pedido auditável" in auditoria
    assert resposta in auditoria


def test_planejar_propaga_cota_esgotada(tmp_path, monkeypatch):
    _preparar_sprint(tmp_path)
    spec = tmp_path / "quota.json"
    spec.write_text(json.dumps({"acao": "quota"}), encoding="utf-8")
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    monkeypatch.setenv("AUTODEV_FAKE_SPEC", str(spec))

    with pytest.raises(CotaEsgotada):
        planejar(tmp_path, "Planeje sem esconder a cota", "codex")


def test_planejar_cita_inicio_da_resposta_sem_json(tmp_path, monkeypatch):
    _preparar_sprint(tmp_path)
    resposta = "RESPOSTA INESPERADA: nenhum plano foi produzido"
    _configurar_fake(monkeypatch, tmp_path, resposta)

    with pytest.raises(PlanoInvalido, match="RESPOSTA INESPERADA"):
        planejar(tmp_path, "Planeje", "codex")
