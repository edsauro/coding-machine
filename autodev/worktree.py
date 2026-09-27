"""DEVFACTORY — gerente de worktrees Git (T04, spec §15).

Regras:
  * um worktree por task, nomeado <sprint>-<task>-<agente>;
  * branch sprint/<sprint>/<task>-<agente>;
  * criação idempotente (spec §22): se o worktree já existe e é válido, reusa;
  * um único escritor por worktree, garantido pelo StateStore.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitErro(Exception):
    pass


def git(*args: str, cwd: str | Path, check: bool = True) -> str:
    p = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise GitErro(f"git {' '.join(args)} falhou ({p.returncode}): "
                      f"{(p.stderr or p.stdout).strip()[:400]}")
    return (p.stdout or "").strip()


def commit_atual(repo: str | Path) -> str:
    return git("rev-parse", "HEAD", cwd=repo)


def branch_existe(repo: str | Path, branch: str) -> bool:
    p = subprocess.run(["git", "rev-parse", "--verify", "--quiet", branch],
                       cwd=str(repo), capture_output=True, text=True)
    return p.returncode == 0


def esta_limpo(repo: str | Path) -> bool:
    return git("status", "--porcelain", cwd=repo) == ""


def arquivos_alterados(repo: str | Path, base: str | None = None) -> list[str]:
    """Arquivos alterados vs base (ou vs HEAD se base vazio)."""
    if base:
        out = git("diff", "--name-only", base, cwd=repo)
        out += "\n" + git("ls-files", "--others", "--exclude-standard", cwd=repo)
    else:
        out = git("status", "--porcelain", cwd=repo)
        return [l[3:].strip() for l in out.splitlines() if l.strip()]
    return sorted({l.strip() for l in out.splitlines() if l.strip()})


@dataclass
class Worktree:
    caminho: Path
    branch: str
    base_commit: str
    criado: bool = False


class WorktreeManager:
    def __init__(self, repo: str | Path, raiz_worktrees: str | Path):
        self.repo = Path(repo).resolve()
        self.raiz = Path(raiz_worktrees).resolve()
        self.raiz.mkdir(parents=True, exist_ok=True)

    def caminho_para(self, sprint: str, task: str, agente: str) -> Path:
        return self.raiz / f"{sprint}-{task}-{agente}"

    def branch_para(self, sprint: str, task: str, agente: str) -> str:
        return f"sprint/{sprint}/{task}-{agente}"

    def criar(self, sprint: str, task: str, agente: str,
              base: str | None = None) -> Worktree:
        """Idempotente: reusa worktree/branch válidos em vez de recriar."""
        caminho = self.caminho_para(sprint, task, agente)
        branch = self.branch_para(sprint, task, agente)
        base = base or commit_atual(self.repo)

        if caminho.exists() and (caminho / ".git").exists():
            atual = git("rev-parse", "--abbrev-ref", "HEAD", cwd=caminho)
            return Worktree(caminho, atual, base, criado=False)

        # branch órfã sem worktree: reaproveita
        if not branch_existe(self.repo, branch):
            git("branch", branch, base, cwd=self.repo)
        git("worktree", "add", str(caminho), branch, cwd=self.repo)
        return Worktree(caminho, branch, base, criado=True)

    def remover(self, caminho: str | Path, force: bool = True) -> None:
        args = ["worktree", "remove", str(caminho)]
        if force:
            args.append("--force")
        git(*args, cwd=self.repo, check=False)

    def listar(self) -> list[dict]:
        out = git("worktree", "list", "--porcelain", cwd=self.repo)
        itens, atual = [], {}
        for linha in out.splitlines():
            if linha.startswith("worktree "):
                if atual:
                    itens.append(atual)
                atual = {"caminho": linha.split(" ", 1)[1]}
            elif linha.startswith("branch "):
                atual["branch"] = linha.split(" ", 1)[1].replace("refs/heads/", "")
            elif linha.startswith("HEAD "):
                atual["head"] = linha.split(" ", 1)[1]
        if atual:
            itens.append(atual)
        return itens

    def commit(self, caminho: str | Path, mensagem: str) -> str | None:
        """Commita o que houver. Devolve o SHA, ou None se nada mudou."""
        git("add", "-A", cwd=caminho)
        if git("diff", "--cached", "--name-only", cwd=caminho) == "":
            return None
        git("-c", "user.email=autodev@localhost", "-c", "user.name=autodev",
            "commit", "-q", "-m", mensagem, cwd=caminho)
        return commit_atual(caminho)

    def merge(self, destino: str | Path, branch: str, mensagem: str) -> tuple[bool, str]:
        """Merge com --no-ff. Não faz merge em main (spec §23)."""
        p = subprocess.run(
            ["git", "-c", "user.email=autodev@localhost", "-c", "user.name=autodev",
             "merge", "--no-ff", "-m", mensagem, branch],
            cwd=str(destino), capture_output=True, text=True)
        return p.returncode == 0, (p.stdout + p.stderr).strip()[:1500]
