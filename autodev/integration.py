"""DEVFACTORY — integração no branch do Sprint (T12, spec §23).

NUNCA faz merge em main/produção no Sprint 1. Tudo vai para
sprint/<id>/integration, e só depois de passar os portões.

Portões (na ordem): merge -> build -> testes unitários -> testes de integração ->
lint/type -> segurança -> aceitação do Sprint. Falha em qualquer portão devolve a
task responsável para retry/review.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import testrunner
from .worktree import GitErro, WorktreeManager, commit_atual, git


@dataclass
class Portao:
    nome: str
    ok: bool
    detalhe: str = ""
    comando: str = ""
    duracao: float = 0.0

    def to_dict(self) -> dict:
        return {"nome": self.nome, "ok": self.ok, "detalhe": self.detalhe[:2000],
                "comando": self.comando, "duracao": round(self.duracao, 2)}


@dataclass
class ResultadoIntegracao:
    task_id: str
    branch: str
    merge_ok: bool
    commit: str = ""
    portoes: list[Portao] = field(default_factory=list)
    conflito: str = ""

    @property
    def ok(self) -> bool:
        return self.merge_ok and all(p.ok for p in self.portoes)

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "branch": self.branch,
                "merge_ok": self.merge_ok, "commit": self.commit,
                "ok": self.ok, "conflito": self.conflito,
                "portoes": [p.to_dict() for p in self.portoes]}


class Integrador:
    def __init__(self, repo: str | Path, branch: str, sprint: str | None = None):
        self.repo = Path(repo).resolve()
        self.branch = branch
        # O worktree de integração precisa ser POR SPRINT: dois sprints no mesmo
        # repositório não podem disputar o mesmo diretório.
        self.sprint = sprint or self._sprint_do_branch(branch)
        self.wm = WorktreeManager(self.repo, self.repo / ".autodev" / "worktrees")

    @staticmethod
    def _sprint_do_branch(branch: str) -> str:
        partes = branch.split("/")
        return partes[1] if len(partes) >= 3 and partes[0] == "sprint" else "sprint"

    def garantir_worktree(self, base: str | None = None) -> Path:
        """Cria/reusa o worktree de integração. Idempotente."""
        caminho = self.wm.caminho_para(self.sprint, "integration", "int")
        if caminho.exists() and (caminho / ".git").exists():
            return caminho
        base = base or commit_atual(self.repo)
        if not self._branch_existe():
            git("branch", self.branch, base, cwd=self.repo)
        git("worktree", "add", str(caminho), self.branch, cwd=self.repo)
        return caminho

    def _branch_existe(self) -> bool:
        p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", self.branch],
                           cwd=str(self.repo), capture_output=True, text=True)
        return p.returncode == 0

    def merge_task(self, task_id: str, branch: str, caminho_wt: Path) -> ResultadoIntegracao:
        r = ResultadoIntegracao(task_id=task_id, branch=branch, merge_ok=False)
        p = subprocess.run(
            ["git", "-c", "user.email=autodev@localhost", "-c", "user.name=autodev",
             "merge", "--no-ff", "-m", f"integrate {task_id}: {branch}", branch],
            cwd=str(caminho_wt), capture_output=True, text=True)
        r.merge_ok = p.returncode == 0
        if r.merge_ok:
            r.commit = commit_atual(caminho_wt)
        else:
            r.conflito = (p.stdout + p.stderr).strip()[:2000]
            subprocess.run(["git", "merge", "--abort"], cwd=str(caminho_wt),
                           capture_output=True)
        return r

    def rodar_portoes(self, caminho_wt: Path, *,
                      comandos: dict[str, str] | None = None,
                      evidencia_dir: str | Path | None = None) -> list[Portao]:
        """Portões objetivos. Cada um é um comando; ausência de comando = pulado."""
        import time
        comandos = comandos or {}
        portoes: list[Portao] = []

        # 1. build (se declarado, ou se houver pyproject/package.json)
        build = comandos.get("build")
        if not build:
            if (Path(caminho_wt) / "pyproject.toml").exists():
                build = "python3 -m compileall -q ."
            elif (Path(caminho_wt) / "package.json").exists():
                build = "npm run build --silent"
        if build:
            portoes.append(self._portao("build", build, caminho_wt))
        else:
            portoes.append(Portao("build", True, "sem etapa de build aplicavel"))

        # 2. testes (determinístico)
        t0 = time.time()
        rt = testrunner.rodar(caminho_wt, comando=comandos.get("testes"),
                              evidencia_dir=evidencia_dir, rotulo="integracao")
        portoes.append(Portao("testes", rt.passou,
                              f"exit={rt.exit_code} passed={rt.passed} failed={rt.failed}\n"
                              + rt.saida[-1200:], rt.comando, time.time() - t0))

        # 3. lint/type (opcional e não bloqueante se a ferramenta faltar)
        lint = comandos.get("lint")
        if not lint:
            for cand in ("ruff", "flake8"):
                if shutil.which(cand):
                    lint = f"{cand} check ."
                    break
        if lint:
            portoes.append(self._portao("lint", lint, caminho_wt))
        else:
            portoes.append(Portao("lint", True, "nenhum linter instalado"))

        # 4. segurança — sempre roda, é portão duro
        portoes.append(self._portao_seguranca(caminho_wt))
        return portoes

    def _portao(self, nome: str, comando: str, cwd: Path) -> Portao:
        import time
        t0 = time.time()
        try:
            p = subprocess.run(comando.split(), cwd=str(cwd), capture_output=True,
                               text=True, timeout=900)
            return Portao(nome, p.returncode == 0,
                          (p.stdout + p.stderr)[-1500:], comando, time.time() - t0)
        except Exception as e:  # noqa: BLE001
            return Portao(nome, False, str(e), comando, time.time() - t0)

    def _portao_seguranca(self, cwd: Path) -> Portao:
        """Checagens duras de segurança (spec §18/§23). Falha = merge rejeitado."""
        import time
        t0 = time.time()
        problemas: list[str] = []
        # nenhum arquivo de segredo/credencial no repositório
        for padrao in ("**/.env", "**/*.pem", "**/*.key", "**/id_rsa",
                       "**/credentials.json", "**/auth.json"):
            for f in Path(cwd).glob(padrao):
                if ".git/" not in str(f):
                    problemas.append(f"arquivo sensivel versionado: {f}")
        # nada escrevendo em caminho absoluto do HOME
        import re
        for f in Path(cwd).rglob("*.py"):
            if ".git" in f.parts:
                continue
            try:
                t = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for m in re.finditer(r"(?m)^\s*.*open\(\s*['\"]/home/", t):
                problemas.append(f"escrita em caminho absoluto do HOME: {f}")
                break
        # segredo embutido no código (spec §18) — portão duro
        problemas.extend(self._segredos_embutidos(Path(cwd)))
        ok = not problemas
        return Portao("seguranca", ok,
                      "; ".join(problemas) if problemas else "sem achados",
                      "(checagens internas)", time.time() - t0)

    # Formatos de segredo que aparecem colados no código. São deliberadamente
    # específicos: um padrão genérico tipo `senha = "..."` daria falso positivo
    # em testes e documentação.
    PADROES_SEGREDO = (
        re.compile(r"\bsk-[A-Za-z0-9]{12,}"),
        re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}"),
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
        re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
    )
    EXTENSOES_SEGREDO = {".py", ".js", ".ts", ".go", ".rb", ".sh", ".yaml",
                         ".yml", ".json", ".toml", ".env", ".txt", ".md", ".cfg", ".ini"}

    @classmethod
    def _segredos_embutidos(cls, cwd: Path) -> list[str]:
        """Segredos em texto claro versionados no repositório."""
        achados: list[str] = []
        for f in cwd.rglob("*"):
            if not f.is_file() or ".git" in f.parts:
                continue
            if f.suffix.lower() not in cls.EXTENSOES_SEGREDO:
                continue
            if f.stat().st_size > 2_000_000:
                continue
            try:
                txt = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for padrao in cls.PADROES_SEGREDO:
                m = padrao.search(txt)
                if m:
                    linha = txt[:m.start()].count("\n") + 1
                    achados.append(
                        f"segredo embutido em {f.relative_to(cwd)}:{linha} "
                        f"({padrao.pattern[:24]}…)")
                    break
        return achados

    def portao_aceitacao(self, caminho_wt: Path, comando: str) -> Portao:
        """Portão final: os critérios de aceitação do Sprint (spec §23/§24)."""
        return self._portao("aceitacao_sprint", comando, caminho_wt)
