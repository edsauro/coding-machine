"""DEVFACTORY — isolamento de execução via bubblewrap (T07, spec §7/§18).

Por que bwrap e não Docker: o spec prioriza simplicidade e proíbe infra pesada.
bwrap é um binário único, sem daemon, sem root, com namespace de mount. Docker
existe na máquina mas adiciona daemon, imagens e um modelo de permissão que
precisaria de root — desproporcional para Sprint 1.

O sandbox monta:
  * sistema (/, /usr, /lib...) como SOMENTE LEITURA;
  * o worktree da task como leitura+escrita;
  * um /tmp privado e efêmero;
  * o HOME real NÃO é montado — caminhos proibidos ficam inalcançáveis.

A rede permanece LIGADA por padrão: os agentes chamam APIs remotas. Desligar
quebraria Codex/AGY. O que se restringe é o sistema de arquivos.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

BWRAP = shutil.which("bwrap")

# HOME efêmero usado dentro do sandbox (fica no repo, não no HOME real).
RAIZ_HOME_SANDBOX = Path(__file__).resolve().parent.parent / ".autodev" / "sandbox-home"

# Diretórios de sistema montados somente-leitura dentro do sandbox.
RO_BINDS = ["/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc/ssl",
            "/etc/ca-certificates", "/etc/resolv.conf", "/etc/hosts",
            "/etc/nsswitch.conf", "/etc/passwd", "/etc/group", "/opt"]


@dataclass
class SandboxSpec:
    worktree: str
    permite_rede: bool = True
    ro_extra: list[str] | None = None
    rw_extra: list[str] | None = None


def disponivel() -> bool:
    return BWRAP is not None


def caminhos_proibidos_presentes(caminhos: list[str]) -> list[str]:
    """Confere que os caminhos proibidos de fato existem (para o teste provar
    que o sandbox os esconde)."""
    return [c for c in caminhos if Path(c.replace("~", str(Path.home()))).exists()]


def _auto_ro() -> list[str]:
    """Caminhos que o sandbox PRECISA montar só-leitura para o toolchain existir.

    Sem isto o interpretador do projeto (venv) simplesmente não existe dentro do
    sandbox e todo comando Python falha com "No such file or directory" — bug
    real encontrado pelo teste do portão de testes.

    Duas sutilezas que custaram tempo:
      1. `sys.prefix` (o venv) e `sys.base_prefix` (a instalação real) são
         caminhos DIFERENTES — é preciso montar os dois;
      2. o `bin/python3.11` do venv é um SYMLINK cujo alvo pode divergir do
         `base_prefix` (ex.: uv aponta para `cpython-3.11-...` enquanto o prefixo
         é `cpython-3.11.16-...`). Se o alvo não for montado, o symlink fica
         pendurado e o execvp falha. Por isso resolvemos o executável real e
         montamos o prefixo dele também.
    """
    import sys
    fora: list[str] = []
    candidatos = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()]
    real = Path(sys.executable).resolve()
    candidatos.append(real.parent.parent)          # prefixo real do binário
    candidatos.append(real.parent)                 # .../bin, para o symlink valer
    # O DIRETÓRIO PAI do prefixo real: é lá que vive o symlink intermediário.
    # No uv, `.../python/cpython-3.11-linux-x86_64-gnu` é um symlink para
    # `.../python/cpython-3.11.16-linux-x86_64-gnu`; sem montar o pai, o symlink
    # do venv fica pendurado dentro do sandbox e o execvp falha.
    pai = Path(sys.base_prefix).resolve().parent
    if pai not in (Path.home(), Path("/")):
        candidatos.append(pai)
    for p in candidatos:
        s = str(p)
        if s not in ("/", "/usr", "/usr/local") and s not in fora:
            fora.append(s)
    return fora


def montar_cmd(spec: SandboxSpec) -> list[str]:
    """Monta a linha de comando do bwrap. Devolve [] se bwrap não existe.

    ATENÇÃO ao `--tmpfs /home`: ele MASCARA o worktree, porque os worktrees vivem
    sob /home. O namespace do bwrap já começa vazio — nada é visível sem bind —,
    então o HOME real fica inacessível sem precisar de tmpfs. Basta montar o
    worktree e um HOME efêmero em /tmp/home.
    """
    if not BWRAP:
        return []
    wt = str(Path(spec.worktree).resolve())
    cmd = [BWRAP, "--die-with-parent", "--unshare-pid", "--unshare-uts",
           "--unshare-ipc", "--new-session", "--chdir", wt]
    for d in RO_BINDS:
        if Path(d).exists():
            cmd += ["--ro-bind-try", d, d]
    for d in _auto_ro() + (spec.ro_extra or []):
        cmd += ["--ro-bind-try", d, d]
    # /tmp privado e efêmero + HOME em cima dele
    cmd += ["--tmpfs", "/tmp"]
    home_efemero = preparar_home()
    cmd += ["--bind", str(home_efemero), "/tmp/home"]
    cmd += ["--bind", wt, wt]
    for d in (spec.rw_extra or []):
        if Path(d).exists():
            cmd += ["--bind", d, d]
    cmd += ["--proc", "/proc", "--dev", "/dev"]
    if not spec.permite_rede:
        cmd += ["--unshare-net"]
    cmd += ["--setenv", "HOME", "/tmp/home"]
    # Marcador explícito: a suíte do projeto pode conter testes que só fazem
    # sentido rodando no HOST (os que verificam o isolamento a partir de fora).
    # Sem um marcador eles tentam ANINHAR sandbox e confundem o HOME efêmero
    # com o real — foi assim que a suíte do próprio Coding_Machine passou a
    # falhar 2 testes dentro do sandbox e derrubava toda task da sprint.
    cmd += ["--setenv", "AUTODEV_SANDBOX", "1"]
    return cmd


def preparar_home() -> Path:
    """HOME efêmero, estável entre execuções, com o mínimo para autenticar.

    O HOME real nunca é montado. Só a credencial do Codex é COPIADA para cá —
    e o arquivo fica com permissão 600.

    Já DENTRO de um sandbox não se prepara outro. O HOME ali já é o efêmero
    /tmp/home, e criar um aninhado tem um efeito colateral grave: `__file__`
    aponta para o sandbox.py do WORKTREE em teste, então o HOME sintético —
    com uma cópia do token — era escrito dentro do próprio worktree. O portão
    de segurança encontrava o token lá e reprovava a integração inteira do
    sprint. Não é hipótese: aconteceu no primeiro backlog multi-task, e a
    mensagem ("arquivo sensivel versionado: .../auth.json") não apontava para
    o sandbox como causa.
    """
    if os.environ.get("AUTODEV_SANDBOX") == "1":
        return Path(os.environ.get("HOME") or "/tmp/home")
    destino = RAIZ_HOME_SANDBOX
    (destino / ".codex").mkdir(parents=True, exist_ok=True)
    (destino / ".config").mkdir(parents=True, exist_ok=True)
    (destino / ".cache").mkdir(parents=True, exist_ok=True)
    (destino / ".gitconfig").write_text(
        "[user]\n\tname = autodev\n\temail = autodev@localhost\n", encoding="utf-8")
    origem = Path.home() / ".codex" / "auth.json"
    alvo = destino / ".codex" / "auth.json"
    if origem.exists():
        try:
            shutil.copy2(origem, alvo)
            alvo.chmod(0o600)
        except Exception:  # noqa: BLE001
            pass
    # config.toml também é necessário: sem ele o CLI perde defaults de modelo e
    # de sandbox e pode falhar ou se comportar de forma diferente do esperado.
    # Não contém segredo — são só preferências.
    for nome in ("config.toml", "models_cache.json"):
        o2 = Path.home() / ".codex" / nome
        if o2.exists():
            try:
                shutil.copy2(o2, destino / ".codex" / nome)
            except Exception:  # noqa: BLE001
                pass
    return destino


def rodar(spec: SandboxSpec, comando: list[str], timeout: int = 600,
          cwd: str | None = None) -> subprocess.CompletedProcess:
    """Executa `comando` dentro do sandbox (ou direto, se bwrap faltar)."""
    if not BWRAP:
        return subprocess.run(comando, capture_output=True, text=True,
                              timeout=timeout, cwd=cwd or spec.worktree)
    cmd = montar_cmd(spec) + ["--"] + comando
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          cwd=cwd or spec.worktree)


def verificar_isolamento(spec: SandboxSpec, proibidos: list[str]) -> dict:
    """Prova executável de que o sandbox esconde os caminhos proibidos.

    Roda `test -r <caminho>` dentro do sandbox para cada caminho proibido que
    existe no host: dentro, todos devem falhar.
    """
    if not BWRAP:
        return {"sandbox": False, "motivo": "bwrap ausente", "escondidos": [],
                "visiveis": []}
    escondidos, visiveis = [], []
    for c in caminhos_proibidos_presentes(proibidos):
        real = c.replace("~", str(Path.home()))
        p = rodar(spec, ["sh", "-c", f"test -r '{real}' && echo SIM || echo NAO"],
                  timeout=30)
        (visiveis if "SIM" in p.stdout else escondidos).append(real)
    return {"sandbox": True, "escondidos": escondidos,
            "visiveis": visiveis, "ok": not visiveis}
