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


# Ruído de execução NUNCA é entrega. Achado real: num projeto que rastreia
# __pycache__, rodar os testes "altera" os .pyc — e a tentativa que não entregou
# NADA passaria por entregue (e a onda, por integrada). O repo real não rastreia
# bytecode; o filtro é a proteção que não depende da higiene de cada projeto.
RUIDO_ENTREGA = ("__pycache__/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/",
                 ".tox/", ".nox/", "node_modules/")
SUFIXOS_RUIDO = (".pyc", ".pyo", ".pyd")


def e_ruido(caminho: str) -> bool:
    """Bytecode/cache de ferramenta: não conta como mudança entregue."""
    c = caminho.strip().lstrip("./")
    return c.endswith(SUFIXOS_RUIDO) or any(r in c for r in RUIDO_ENTREGA)


def arquivos_alterados(repo: str | Path, base: str | None = None) -> list[str]:
    """Arquivos alterados vs base (ou vs HEAD se base vazio), sem ruído de execução."""
    if base:
        out = git("diff", "--name-only", base, cwd=repo)
        out += "\n" + git("ls-files", "--others", "--exclude-standard", cwd=repo)
    else:
        out = git("status", "--porcelain", cwd=repo)
        return [l[3:].strip() for l in out.splitlines() if l.strip() and not e_ruido(l[3:])]
    return sorted({l.strip() for l in out.splitlines() if l.strip() and not e_ruido(l)})


def git_ok(*args: str, cwd: str | Path) -> bool:
    """git que só interessa saber se deu certo."""
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True).returncode == 0


@dataclass
class Worktree:
    caminho: Path
    branch: str
    base_commit: str
    criado: bool = False
    realinhado: bool = False      # o worktree foi trazido para a base atual
    base_antiga: str = ""         # sha de onde ele veio (auditoria)


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
        """Idempotente: reusa worktree/branch, mas SEMPRE na base atual.

        D-18: `git worktree add <caminho> <branch>` com branch já existente IGNORA
        a base pedida — a task nascia no commit antigo, sem o código das
        dependências já integradas. O DAG era respeitado para ORDEM e violado em
        CONTEÚDO: P04 (base pré-P02/P03) e P03 editaram `autodev/planner.py` e o
        merge conflitou na integração, devolvendo a task para retry sem que ela
        tivesse culpa. A dica está em `_base_do_worktree`: a integração acontece a
        cada onda, então o branch de uma task tem de nascer do tip de integração —
        inclusive quando a branch vem de uma rodada anterior.
        """
        caminho = self.caminho_para(sprint, task, agente)
        branch = self.branch_para(sprint, task, agente)
        base = base or commit_atual(self.repo)

        if caminho.exists() and (caminho / ".git").exists():
            atual = git("rev-parse", "--abbrev-ref", "HEAD", cwd=caminho)
            realinhado, antiga = self._realinhar(caminho, base, branch)
            return Worktree(caminho, atual, base, criado=False,
                            realinhado=realinhado, base_antiga=antiga)

        antiga = ""
        if not branch_existe(self.repo, branch):
            git("branch", branch, base, cwd=self.repo)
        elif not git_ok("merge-base", "--is-ancestor", base, branch, cwd=self.repo):
            # branch de rodada anterior: arquiva o tip e devolve para a base atual
            antiga = git("rev-parse", branch, cwd=self.repo)
            if antiga:
                git("update-ref", f"refs/arquivo/{branch}", antiga, cwd=self.repo)
            git("branch", "-f", branch, base, cwd=self.repo)
        git("worktree", "add", str(caminho), branch, cwd=self.repo)
        return Worktree(caminho, branch, base, criado=True,
                        realinhado=bool(antiga), base_antiga=antiga)

    def _realinhar(self, caminho: Path, base: str, branch: str) -> tuple[bool, str]:
        """Traz um worktree existente para a base atual. Devolve (mudou, sha_antigo).

        Caso 1 — a base já está contida no HEAD: nada a fazer (retry normal, mesma
        onda). Caso 2 — falta o código de dependência integrada depois: faz MERGE
        da base dentro do worktree, preservando os commits da própria task. Caso 3
        — o merge conflita: o trabalho antigo foi escrito contra um código que não
        existe mais (daria regressão na revisão), então o tip antigo fica
        arquivado em `refs/arquivo/<branch>` e o worktree volta para a base.
        """
        head = git("rev-parse", "HEAD", cwd=caminho)
        if not head or head == base or git_ok("merge-base", "--is-ancestor", base, head,
                                              cwd=caminho):
            return False, ""
        m = subprocess.run(["git", "merge", "--no-edit", base], cwd=str(caminho),
                           capture_output=True, text=True)
        if m.returncode == 0:
            return True, head
        subprocess.run(["git", "merge", "--abort"], cwd=str(caminho),
                       capture_output=True, text=True)
        git("update-ref", f"refs/arquivo/{branch}", head, cwd=self.repo)
        git("reset", "--hard", base, cwd=caminho)
        return True, head

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
