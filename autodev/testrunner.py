"""DEVFACTORY — runner de testes determinístico (T08, spec §17/§23).

Nenhuma task é DONE porque um agente disse que terminou: a evidência é a saída
de um comando de teste reproduzível. O runner:
  * descobre o comando de teste do projeto (ou usa o configurado no sprint.yaml);
  * roda DENTRO do sandbox, no worktree da task;
  * persiste a saída integral como evidência;
  * devolve um resultado estruturado (passou/falhou, contagens, assinatura).
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox

DETECTORES = [
    # SEM `-q` próprio: se o projeto já configura `-q` no pyproject/pytest.ini, o
    # nosso somaria e viraria `-qq`, que SUPRIME a linha de resumo e deixa o
    # runner sem saber quantos testes passaram. Bug real encontrado pelos testes.
    ("pytest", ["pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"],
     ["python3", "-m", "pytest", "--no-header", "-p", "no:cacheprovider"]),
    ("pytest-tests", ["tests"],
     ["python3", "-m", "pytest", "--no-header", "-p", "no:cacheprovider"]),
    ("unittest", ["test", "tests"], ["python3", "-m", "unittest", "discover", "-v"]),
    ("npm", ["package.json"], ["npm", "test", "--silent"]),
    ("make", ["Makefile"], ["make", "test"]),
    ("go", ["go.mod"], ["go", "test", "./..."]),
]

PADROES = [
    (re.compile(r"(\d+) passed"), "passed"),
    (re.compile(r"(\d+) failed"), "failed"),
    (re.compile(r"(\d+) error"), "errors"),
    (re.compile(r"Ran (\d+) test"), "total"),
    (re.compile(r"OK\b.*"), "ok"),
    (re.compile(r"FAILED\b"), "ok"),
]

# Linha de progresso do pytest: "..F.s.  [ 60%]"
_PROGRESSO = re.compile(r"^\s*([.FEsxX]+)\s*\[\s*\d+%\]", re.M)


def _contar_progresso(saida: str) -> tuple[int, int]:
    """Fallback: conta pontos/F da linha de progresso quando não há resumo.

    Acontece quando o projeto força quietude extra (`-qq`) e a linha
    "N passed" nunca é impressa.
    """
    pontos = falhas = 0
    for m in _PROGRESSO.finditer(saida):
        for ch in m.group(1):
            if ch == ".":
                pontos += 1
            elif ch in "FExX":
                falhas += 1
    return pontos, falhas


@dataclass
class ResultadoTeste:
    comando: str
    exit_code: int
    saida: str = ""
    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    duracao: float = 0.0
    detectado_por: str = ""
    assinatura: str = ""
    execucao_ok: bool = False          # o comando rodou (mesmo que os testes falhem)
    evidencias: list[str] = field(default_factory=list)

    @property
    def passou(self) -> bool:
        return self.execucao_ok and self.exit_code == 0

    def to_dict(self) -> dict:
        return {"comando": self.comando, "exit_code": self.exit_code,
                "passed": self.passed, "failed": self.failed,
                "errors": self.errors, "duracao": round(self.duracao, 2),
                "detectado_por": self.detectado_por, "assinatura": self.assinatura,
                "execucao_ok": self.execucao_ok, "passou": self.passou,
                "evidencias": self.evidencias}


def resumo_testes(saida: str) -> tuple[list[str], list[str]]:
    """Separa nomes de testes que passam dos que falham (spec §12).

    Usado no prompt de continuação pós-cota: o agente que retoma precisa saber
    exatamente o que já está funcionando e o que ainda falta. Depende das linhas
    `PASSED`/`FAILED`/`ERROR` do resumo curto do pytest.
    """
    passando: list[str] = []
    falhando: list[str] = []
    for m in re.finditer(r"(?m)^\s*(PASSED|FAILED|ERROR)\s+(\S+)", saida):
        (passando if m.group(1) == "PASSED" else falhando).append(m.group(2))
    if not falhando:
        falhando = [m.group(1) for m in
                    re.finditer(r"(?m)^FAILED\s+(\S+)", saida)]
    return passando, falhando


def _python() -> str:
    """Interpretador do PROJETO, não o do PATH.

    Bug real encontrado pelos testes: usar `python3` cru resolvia para o venv do
    Hermes (que não tem pytest), e todo comando Python falhava com "No module
    named pytest" — mascarado como falha de teste. O interpretador correto é o
    mesmo que roda o orquestrador.
    """
    return sys.executable or "python3"


def _resolver(cmd: list[str]) -> list[str]:
    """Troca o `python3` simbólico pelo interpretador real do projeto."""
    if cmd and cmd[0] == "python3":
        return [_python(), *cmd[1:]]
    return list(cmd)


def detectar_comando(worktree: str | Path, override: str | None = None) -> tuple[str, list[str]]:
    """Descobre como testar este projeto. Override explícito tem precedência."""
    wt = Path(worktree)
    if override:
        return "configurado", _resolver(override.split())
    for nome, sinais, cmd in DETECTORES:
        for s in sinais:
            if (wt / s).exists():
                # pytest só se houver testes de fato
                if cmd[1] == "pytest":
                    if not any(wt.rglob("test_*.py")) and not any(wt.rglob("*_test.py")):
                        continue
                return nome, _resolver(cmd)
    return "nenhum", []


def _assinatura(saida: str, exit_code: int) -> str:
    """Assinatura estável da falha — usada no fingerprint anti-loop (spec §11).

    Normaliza números e caminhos efêmeros para que a mesma falha produza a mesma
    assinatura em execuções diferentes.
    """
    linhas = []
    for l in saida.splitlines():
        if re.search(r"(FAIL|ERROR|Error|assert|Traceback|Exception|expected|got)", l):
            n = re.sub(r"\d+", "#", l)
            n = re.sub(r"(/[\w.\-]+)+", "<path>", n)
            linhas.append(n.strip()[:200])
    if not linhas:
        linhas = [f"exit={exit_code}"]
    base = "\n".join(sorted(set(linhas))[:12])
    return hashlib.sha256(base.encode()).hexdigest()[:16]


def rodar(worktree: str | Path, *, comando: str | None = None,
          timeout: int = 600, usar_sandbox: bool = True,
          evidencia_dir: str | Path | None = None,
          rotulo: str = "teste") -> ResultadoTeste:
    """Roda a suíte de testes do worktree e devolve resultado estruturado."""
    nome, cmd = detectar_comando(worktree, comando)
    if not cmd:
        return ResultadoTeste(comando="(nenhum)", exit_code=127,
                              saida="nenhum comando de teste detectado",
                              detectado_por="nenhum", execucao_ok=False,
                              assinatura=_assinatura("nenhum comando de teste", 127))
    t0 = time.time()
    spec = sandbox.SandboxSpec(worktree=str(worktree))
    try:
        if usar_sandbox and sandbox.disponivel():
            p = sandbox.rodar(spec, cmd, timeout=timeout)
            saida = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
            code = p.returncode
        else:
            p = subprocess.run(cmd, cwd=str(worktree), capture_output=True,
                               text=True, timeout=timeout)
            saida = (p.stdout or "") + ("\n" + p.stderr if p.stderr else "")
            code = p.returncode
        exec_ok = True
    except subprocess.TimeoutExpired:
        saida, code, exec_ok = f"timeout de {timeout}s", 124, False
    except Exception as e:  # noqa: BLE001
        saida, code, exec_ok = f"erro ao executar: {e}", 127, False

    r = ResultadoTeste(comando=" ".join(cmd), exit_code=code, saida=saida,
                       duracao=time.time() - t0, detectado_por=nome,
                       execucao_ok=exec_ok,
                       assinatura=_assinatura(saida, code))
    for rx, campo in PADROES:
        m = rx.search(saida)
        if m:
            if campo == "passed":
                r.passed = int(m.group(1))
            elif campo == "failed":
                r.failed = int(m.group(1))
            elif campo == "errors":
                r.errors = int(m.group(1))
            elif campo == "total" and m.lastindex:
                r.passed = r.passed or int(m.group(1))
    if r.passed is None or r.failed is None:
        # sem linha de resumo (projeto com -qq) -> conta a barra de progresso
        pts, fai = _contar_progresso(saida)
        if pts or fai:
            r.passed = pts if r.passed is None else r.passed
            r.failed = fai if r.failed is None else r.failed
    if r.failed is None and "passed" in saida:
        r.failed = 0

    if evidencia_dir:
        d = Path(evidencia_dir)
        d.mkdir(parents=True, exist_ok=True)
        arq = d / f"{rotulo}-{int(t0)}.txt"
        arq.write_text(f"$ {' '.join(cmd)}\nexit={code} ({r.duracao:.1f}s)\n\n{saida}",
                       encoding="utf-8")
        r.evidencias.append(str(arq))
    return r


def testes_vermelhos_antes(worktree: str | Path, arquivo_teste: str) -> dict:
    """TDD: prova que o teste novo falha ANTES da implementação (spec §17).

    Devolve {"vermelho": bool, "saida": ...}. Se o teste passar antes, o teste
    não prova nada e a task não pode ser considerada TDD.
    """
    wt = Path(worktree)
    if not (wt / arquivo_teste).exists():
        return {"vermelho": None, "motivo": f"{arquivo_teste} não existe"}
    r = rodar(wt, comando=f"python3 -m pytest {arquivo_teste} -q",
              timeout=300, rotulo="tdd-vermelho")
    return {"vermelho": not r.passou, "exit_code": r.exit_code,
            "saida": r.saida[-2000:], "assinatura": r.assinatura}
