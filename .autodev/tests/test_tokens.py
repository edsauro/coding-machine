"""P-10 — consumo (tokens) por tentativa.

O que estes testes protegem, em ordem de importância:

1. **Nunca estimar.** Sem fonte, o campo fica NULL e o relatório mostra "não medido".
   Um número inventado aqui contaminaria toda comparação de custo entre modelos.
2. **A fonte real.** O rodapé do CLI do Codex vem em DUAS linhas ("tokens used" /
   "36,037") e morria num mktemp apagado no trap do wrapper — o sidecar
   `ASK_CODEX_USO` existe justamente para sobreviver a isso.
3. **A precedência das fontes:** sidecar do wrapper > rodapé em stdout/stderr > nada.
4. **A coluna existe no banco antigo** (a migração é genérica e lê o SCHEMA).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ))

from autodev import agents  # noqa: E402
from autodev.state import StateStore  # noqa: E402

WRAPPER = Path.home() / ".local" / "bin" / "ask-codex"


# --------------------------------------------------------------- 1. o rodapé
def test_rodape_em_duas_linhas():
    assert agents.parse_tokens_do_texto("coisa\ntokens used\n36,037\nfim") == 36037


def test_rodape_inline_e_com_ponto():
    assert agents.parse_tokens_do_texto("tokens used: 1.234.567") == 1234567


def test_vale_o_ultimo_rodape_nunca_a_soma():
    texto = "tokens used\n1,000\nmais saida\ntokens used\n41,900\n"
    assert agents.parse_tokens_do_texto(texto) == 41900


def test_sem_rodape_e_none_nao_zero():
    assert agents.parse_tokens_do_texto("nada de uso aqui") is None
    assert agents.parse_tokens_do_texto("") is None


# --------------------------------------------------------------- 2. o sidecar
def test_ler_uso_json(tmp_path: Path):
    p = tmp_path / "x.uso"
    p.write_text(json.dumps({"rc": 0, "tokens_total": 1234,
                             "fonte": "codex-cli:rodape"}))
    assert agents.ler_uso(p) == (1234, "codex-cli:rodape")


def test_ler_uso_ausente_ou_nulo(tmp_path: Path):
    assert agents.ler_uso(tmp_path / "nao-existe") == (None, "")
    nulo = tmp_path / "nulo.uso"
    nulo.write_text(json.dumps({"rc": 1, "tokens_total": None,
                                "fonte": "codex-cli:rodape-ausente"}))
    total, _ = agents.ler_uso(nulo)
    assert total is None


# --------------------------------------------------------- 3. a precedência
def test_precedencia_sidecar_ganha_do_stdout(tmp_path: Path):
    log = tmp_path / "t.log"
    log.write_text("log")
    Path(str(log) + ".uso").write_text(json.dumps({"tokens_total": 111, "fonte": "wrapper"}))
    res = agents.Resultado(exit_code=0, stdout="tokens used\n999\n")
    agents._medir_tokens(res, log)
    assert (res.tokens_total, res.tokens_fonte) == (111, "wrapper")


def test_sem_sidecar_cai_no_stdout(tmp_path: Path):
    log = tmp_path / "t.log"
    log.write_text("log")
    res = agents.Resultado(exit_code=0, stdout="tokens used\n999\n")
    agents._medir_tokens(res, log)
    assert (res.tokens_total, res.tokens_fonte) == (999, "cli:stdout")


def test_sem_fonte_nenhuma_fica_none(tmp_path: Path):
    log = tmp_path / "t.log"
    log.write_text("log")
    res = agents.Resultado(exit_code=0, stdout="sem rodape")
    agents._medir_tokens(res, log)
    assert res.tokens_total is None and res.tokens_fonte == ""


# --------------------------------------------------- 4. o banco e a migração
def test_gravar_tokens_roundtrip(tmp_path: Path):
    st = StateStore(tmp_path / "state.db")
    st.iniciar_tentativa("S1", "P01", agent="codex", model="m", effort="low",
                         branch="b", worktree="w", sandbox="nenhum", base_commit="c")
    assert st.gravar_tokens("S1", "P01", 1, 4321, "teste") is True
    row = st.ultima_tentativa("S1", "P01")
    assert row["tokens_total"] == 4321 and row["tokens_fonte"] == "teste"


def test_nao_gravar_quando_nao_medido(tmp_path: Path):
    st = StateStore(tmp_path / "state.db")
    st.iniciar_tentativa("S1", "P01", agent="codex", model="m", effort="low",
                         branch="b", worktree="w", sandbox="nenhum", base_commit="c")
    assert st.gravar_tokens("S1", "P01", 1, None, "") is False
    assert st.ultima_tentativa("S1", "P01")["tokens_total"] is None


def test_migracao_adiciona_colunas_em_banco_antigo(tmp_path: Path):
    """Banco criado antes do P-10 não tem as colunas — e o motor precisa delas."""
    caminho = tmp_path / "antigo.db"
    con = sqlite3.connect(caminho)
    con.execute("CREATE TABLE attempts (id INTEGER PRIMARY KEY, sprint_id TEXT,"
                " task_id TEXT, attempt INTEGER, agent TEXT, UNIQUE"
                " (sprint_id, task_id, attempt))")
    con.commit()
    con.close()
    st = StateStore(caminho)                       # abre e reconcilia com o SCHEMA
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(attempts)")}
    assert {"tokens_total", "tokens_fonte"} <= cols
    st.iniciar_tentativa("S1", "P01", agent="codex", model="m", effort="low",
                         branch="b", worktree="w", sandbox="nenhum", base_commit="c")
    assert st.gravar_tokens("S1", "P01", 1, 7, "x") is True


# ------------------------------------------------- 5. a fiação no driver fake
def test_invocar_entrega_tokens_do_driver_fake(tmp_path: Path, monkeypatch):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"acao": "uso", "tokens": 12345, "texto": "[fake]"}))
    monkeypatch.setenv("AUTODEV_FAKE_AGENT", "1")
    # a spec vai em `fake_script`: quem lê AUTODEV_FAKE_SPEC do ambiente é o
    # ORQUESTRADOR (que a repassa para cá) — agents.invocar não olha o ambiente.
    res = agents.invocar(agents.Invocacao(agente="codex", prompt="p",
                                          worktree=str(tmp_path),
                                          log_path=str(tmp_path / "l.log"),
                                          fake_script=str(spec)),
                         cfg=None)
    assert res.tokens_total == 12345
    assert "tokens=12345" in (tmp_path / "l.log").read_text()


# --------------------------------------------- 6. o wrapper de verdade (fake)
@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper ask-codex não instalado")
def test_wrapper_grava_sidecar_com_codex_falso(tmp_path: Path):
    """Prova a integração real, sem gastar cota: um `codex` falso no PATH.

    Foi este teste que pegaria o bug de usar `grep` para um rodapé que vem em
    duas linhas (grep não atravessa linha; o awk sim).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    falso = bin_dir / "codex"
    falso.write_text("#!/usr/bin/env bash\n"
                     'if [ "${1:-}" = "--version" ]; then echo "codex-fake"; exit 0; fi\n'
                     'echo "trabalho"\necho "tokens used"\necho "36,037"\n'
                     'echo "mais"\necho "tokens used"\necho "41,900"\n')
    falso.chmod(0o755)
    uso = tmp_path / "uso.json"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["ASK_CODEX_USO"] = str(uso)
    p = subprocess.run([str(WRAPPER), "-m", "gpt-5.6-luna", "prompt de teste"],
                       capture_output=True, text=True, env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    d = json.loads(uso.read_text())
    assert d["tokens_total"] == 41900          # o ÚLTIMO rodapé, não a soma
    assert Path(str(uso) + ".bruto").exists()


@pytest.mark.skipif(not WRAPPER.exists(), reason="wrapper ask-codex não instalado")
def test_wrapper_sem_variavel_nao_cria_nada(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    falso = bin_dir / "codex"
    falso.write_text("#!/usr/bin/env bash\necho 'tokens used'\necho '10'\n")
    falso.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env.pop("ASK_CODEX_USO", None)
    subprocess.run([str(WRAPPER), "x"], capture_output=True, text=True, env=env,
                   timeout=60)
    assert not list(tmp_path.glob("*.uso*"))
