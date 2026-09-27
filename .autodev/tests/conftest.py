"""Fixtures compartilhadas dos testes do DEVFACTORY."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent.parent   # raiz do repositório
AUTODEV = RAIZ / ".autodev"
sys.path.insert(0, str(RAIZ))

from autodev.config import Config  # noqa: E402
from autodev.state import StateStore  # noqa: E402


@pytest.fixture
def cfg() -> Config:
    return Config.carregar()


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _git(cwd: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr}")
    return p.stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    """Repositório git limpo com um arquivo inicial."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("# repo de teste\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "inicial")
    return r


@pytest.fixture
def projeto_pytest(tmp_path) -> Path:
    """Projeto com pytest, um teste passando e um módulo correto."""
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    (r / "tests").mkdir(parents=True)
    (r / "src" / "calc.py").write_text("def soma(a, b):\n    return a + b\n")
    (r / "tests" / "test_calc.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))\n"
        "from calc import soma\n\n\n"
        "def test_soma():\n    assert soma(2, 3) == 5\n")
    (r / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\naddopts = '-q'\n")
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "inicial")
    return r


def scaffold_sprint(raiz: Path, sprint: str, tarefas: list[dict],
                    objetivo: str = "sprint de teste") -> Path:
    """Cria os arquivos mínimos de um sprint dentro de `raiz`."""
    d = raiz / ".autodev" / "sprints" / sprint
    (d / "evidence").mkdir(parents=True, exist_ok=True)
    (d / "logs").mkdir(parents=True, exist_ok=True)
    (d / "sprint.yaml").write_text(
        f"sprint_id: {sprint}\ntitulo: teste\nobjetivo: {objetivo}\n", encoding="utf-8")
    (d / "spec.md").write_text(f"# {sprint}\n\n{objetivo}\n", encoding="utf-8")
    (d / "dag.json").write_text(
        json.dumps({"sprint_id": sprint, "tasks": tarefas}, ensure_ascii=False),
        encoding="utf-8")
    return d


@pytest.fixture
def fake_agent(monkeypatch):
    """Ativa o driver determinístico de agente."""
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    yield
    monkeypatch.delenv("AUTODEV_FAKE_AGENT", raising=False)


def escreve_fake_spec(p: Path, spec: dict) -> Path:
    p.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    Path(str(p).replace(".json", ".count")).unlink(missing_ok=True)
    return p


def criar_fixture(destino: Path) -> Path:
    """Recria o projeto-fixture de aceitação (T14) no destino dado."""
    import importlib.util
    p = AUTODEV / "fixtures" / "criar_fixture.py"
    spec = importlib.util.spec_from_file_location("criar_fixture_t", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.criar(Path(destino))


TASK_FIXTURE = {
    "id": "T01",
    "titulo": "corrigir mediana e desvio padrao do modulo stats",
    "criterios": [
        "mediana() devolve a mediana real (nao a media)",
        "mediana() nao muta a lista de entrada",
        "desvio_padrao() usa o denominador amostral N-1",
        "amplitude() continua funcionando",
        "todos os testes de tests/test_stats.py passam",
    ],
    "deps": [],
    "agente": "codex",
    "estimativa": "S",
}
