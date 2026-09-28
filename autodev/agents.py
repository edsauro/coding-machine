"""DEVFACTORY — detecção de agentes e adaptadores (T01, T05, T06, spec §8/§12).

Todo acesso a agente externo passa por aqui. O adaptador:
  * escolhe o wrapper correto (ask-codex / ask-agy);
  * persiste modelo/effort REALMENTE usados (spec §9 — nunca inventar nomes);
  * grava log completo em disco;
  * classifica a falha (incluindo cota do Codex) e devolve isso ao orquestrador.

Um driver "fake" existe para os testes determinísticos: ele aplica um patch
conhecido sem gastar cota, e pode simular exaustão de cota sob comando.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import errors
from .config import Config

WRAPPERS = {
    "codex": {"cmd": "ask-codex", "flags": {"modelo": "-m", "effort": "-e",
                                            "dir": "-d", "sandbox": "-s",
                                            "timeout": "--timeout", "arquivo": "-f"},
              # ask-codex aceita o timeout em SEGUNDOS crus
              "timeout_fmt": "{n}"},
    "agy": {"cmd": "ask-agy", "flags": {"modelo": "-m", "effort": "-e",
                                        "modo": "-t", "dir": "-d",
                                        "timeout": "--timeout", "arquivo": "-f",
                                        "permissao": "-D",
                                        "dir_extra": "--add-dir"},
            # ask-agy repassa o valor para `--print-timeout`, que é uma DURAÇÃO:
            # sem unidade ele falha com "missing unit in duration". Bug real
            # encontrado na aceitação com o AGY de verdade.
            "timeout_fmt": "{n}s"},
    # O Hermes entra aqui como REVISOR de verdade (LLM), não como codificador:
    # `hermes -z "<prompt>" -m <modelo>` roda uma sessão headless. Diferenças que
    # o adaptador precisa respeitar: o prompt vai como ARGUMENTO de -z (o CLI não
    # lê prompt por stdin) e não existem -d/--timeout/-f.
    "hermes": {"cmd": "hermes", "flags": {"modelo": "-m", "oneshot": "-z"},
               "timeout_fmt": "{n}", "prompt_arg": True},
}
SANDBOX_FLAG = {"codex": {"editar": "workspace-write", "ler": "read-only"},
                "agy": {"editar": "accept-edits", "ler": "plan"}}


@dataclass
class AgenteInfo:
    nome: str
    disponivel: bool
    versao: str = ""
    binario: str = ""
    wrapper: str = ""
    erro: str = ""


def parse_tokens_do_texto(texto: str) -> int | None:
    """Lê o rodapé `tokens used` do CLI do Codex. Devolve None quando não há.

    Feito linha a linha de propósito: o rodapé real vem em DUAS linhas
    ("tokens used" / "36,037") e um regex guloso de uma linha só casa o dígito
    errado ("1.234.567" virava "7" — bug pego pelo teste). Vale a ÚLTIMA
    ocorrência: o CLI imprime um rodapé por rodada, e somar seria contar o mesmo
    contexto várias vezes.
    """
    achado = None
    linhas = (texto or "").splitlines()
    for i, linha in enumerate(linhas):
        if "tokens used" not in linha.lower():
            continue
        resto = linha.lower().split("tokens used", 1)[1]
        m = re.search(r"[0-9][0-9.,]*", resto)
        if not m:                      # rodapé de duas linhas: o número vem depois
            for seguinte in linhas[i + 1:i + 3]:
                m = re.search(r"[0-9][0-9.,]*", seguinte)
                if m:
                    break
        if m:
            achado = m.group(0)
    if not achado:
        return None
    bruto = achado.replace(",", "").replace(".", "")
    return int(bruto) if bruto.isdigit() else None


def ler_uso(sidecar: Path) -> tuple[int | None, str]:
    """Lê o JSON que o wrapper grava quando ASK_CODEX_USO está definido."""
    try:
        d = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, ""
    n = d.get("tokens_total")
    return (int(n) if isinstance(n, (int, float)) else None), str(d.get("fonte") or "")


def _medir_tokens(res: "Resultado", log_path: Path | None) -> None:
    """Preenche res.tokens_* a partir da fonte mais confiável disponível.

    Ordem: (1) sidecar do wrapper — o CLI imprime o rodapé e o wrapper o captura
    antes de o mktemp morrer; (2) rodapé que tenha vazado para stdout/stderr;
    (3) nada — e aí o campo fica None, que o relatório mostra como "não medido".
    """
    if log_path:
        total, fonte = ler_uso(Path(str(log_path) + ".uso"))
        if total is not None:
            res.tokens_total, res.tokens_fonte = total, fonte or "wrapper"
            return
    for texto, origem in ((res.stdout, "cli:stdout"), (res.stderr, "cli:stderr")):
        total = parse_tokens_do_texto(texto)
        if total is not None:
            res.tokens_total, res.tokens_fonte = total, origem
            return


@dataclass
class Invocacao:
    agente: str
    prompt: str
    worktree: str
    modelo: str | None = None
    effort: str | None = None
    edita: bool = True
    timeout: int = 900
    log_path: str | None = None
    fake_script: str | None = None      # usado só pelo driver fake (testes)


@dataclass
class Resultado:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duracao: float = 0.0
    log_path: str = ""
    prompt: str = ""                    # prompt exato enviado, para auditoria
    failure_class: str | None = None
    modelo_usado: str = ""
    effort_usado: str = ""
    tokens_total: int | None = None      # consumo MEDIDO (None = não medido, nunca 0)
    tokens_fonte: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.failure_class is None


def detectar(config: "Config | None" = None) -> dict[str, AgenteInfo]:
    """T01 — descobre o que existe de fato na máquina. Nunca assume."""
    from .config import Config
    cfg: Config = config or Config.carregar()
    out: dict[str, AgenteInfo] = {}
    for nome, spec in cfg.agents["agentes"].items():
        binario = spec.get("binario", nome)
        caminho = shutil.which(binario)
        wrapper = spec.get("wrapper")
        wrap_ok = True if not wrapper else shutil.which(wrapper) is not None
        info = AgenteInfo(nome=nome, disponivel=bool(caminho) and wrap_ok,
                          binario=caminho or "", wrapper=wrapper or "")
        if not caminho:
            info.erro = f"binario '{binario}' nao encontrado no PATH"
        if caminho:
            try:
                r = subprocess.run([binario, "--version"], capture_output=True,
                                   text=True, timeout=30)
                info.versao = (r.stdout or r.stderr).strip().splitlines()[0][:80]
            except Exception as e:  # noqa: BLE001
                info.erro = str(e)[:120]
        if not wrap_ok:
            info.disponivel = False
            info.erro = f"wrapper {wrapper} ausente"
        out[nome] = info
    return out


def driver_fake_ativo() -> bool:
    return os.environ.get("AUTODEV_FAKE_AGENT", "") == "1"


def invocar(inv: Invocacao, cfg) -> Resultado:
    """Executa o agente no worktree indicado e devolve resultado classificado."""
    t0 = time.time()
    log_path = Path(inv.log_path) if inv.log_path else None
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    if driver_fake_ativo():
        res = _fake(inv)
        res.prompt = inv.prompt
        res.duracao = time.time() - t0
        _gravar_log(log_path, res)
        return res

    if inv.agente not in WRAPPERS:
        return Resultado(exit_code=127, stderr=f"agente sem adaptador: {inv.agente}",
                         failure_class=errors.FailureClass.ENVIRONMENT_ERROR.value,
                         duracao=time.time() - t0)

    w = WRAPPERS[inv.agente]
    modelo = inv.modelo
    effort = inv.effort
    res_effort = "(default)"
    if w.get("prompt_arg"):
        # Revisor Hermes: prompt no argumento, sem -d/--timeout/-f (não existem)
        # e sem -e (effort do Hermes não é flag de CLI). O teto de tempo é o do
        # próprio subprocess.
        cmd = [w["cmd"]]
        if modelo:
            cmd += [w["flags"]["modelo"], modelo]
            res_modelo = modelo
        else:
            res_modelo = "(default do agente)"
        cmd += [w["flags"]["oneshot"], inv.prompt]
    else:
        cmd = [w["cmd"]]
        if modelo:
            cmd += [w["flags"]["modelo"], modelo]
            res_modelo = modelo
        else:
            res_modelo = "(default do agente)"
        if effort:
            cmd += [w["flags"]["effort"], effort]
            res_effort = effort
        cmd += [w["flags"]["dir"], inv.worktree]
        cmd += [w["flags"]["timeout"], w["timeout_fmt"].format(n=inv.timeout)]
        if inv.agente == "codex":
            cmd += [w["flags"]["sandbox"],
                    SANDBOX_FLAG["codex"]["editar" if inv.edita else "ler"]]
        else:
            cmd += [w["flags"]["modo"],
                    SANDBOX_FLAG["agy"]["editar" if inv.edita else "ler"]]
            if inv.edita:
                # O AGY só edita de fato em modo headless com -D. É seguro AQUI
                # porque toda execução de agente roda dentro do worktree + bwrap
                # (HOME real fora de alcance). Sem isto, a edição simplesmente não
                # acontece e a task falha sem motivo aparente.
                cmd += [w["flags"]["permissao"]]
            else:
                # Mesmo em modo plano/revisão o AGY headless precisa de -D: qualquer
                # ferramenta exige a permissão "command", que o modo headless não
                # consegue pedir e AUTO-NEGA em silêncio (stdout vazio, exit 0).
                # Achado real da aceitação. A mitigação é o sandbox, que é
                # exatamente o uso que a própria documentação do AGY recomenda.
                cmd += [w["flags"]["permissao"]]
        # prompt via stdin — evita estourar o limite de argumento e não vaza no ps.
        # O wrapper lê stdin com `-f -`, NÃO com um `-` solto (que ele rejeita como
        # opção desconhecida). Bug real encontrado no teste com o Codex de verdade.
        cmd += [w["flags"]["arquivo"], "-"]

    try:
        # ASK_CODEX_USO: o wrapper grava o consumo ao lado do nosso log ANTES de
        # apagar o mktemp onde o rodapé "tokens used" vive. Opt-in: quem não define
        # a variável não muda de comportamento (ver ~/.local/bin/ask-codex).
        env = os.environ.copy()
        if log_path:
            env.setdefault("ASK_CODEX_USO", str(log_path) + ".uso")
        p = subprocess.run(cmd, input=inv.prompt, capture_output=True, text=True,
                           timeout=inv.timeout + 30, cwd=inv.worktree, env=env)
        res = Resultado(exit_code=p.returncode, stdout=p.stdout or "",
                        stderr=p.stderr or "", modelo_usado=res_modelo,
                        effort_usado=res_effort)
    except subprocess.TimeoutExpired:
        res = Resultado(exit_code=124, stderr=f"timeout de {inv.timeout}s",
                        failure_class=errors.FailureClass.ENVIRONMENT_ERROR.value,
                        modelo_usado=res_modelo, effort_usado=res_effort)
    except FileNotFoundError as e:
        res = Resultado(exit_code=127, stderr=str(e),
                        failure_class=errors.FailureClass.DEPENDENCY_ERROR.value,
                        modelo_usado=res_modelo, effort_usado=res_effort)

    res.duracao = time.time() - t0
    res.prompt = inv.prompt
    if not res.ok or res.exit_code != 0:
        cls = errors.classificar(res.stdout, res.exit_code, res.stderr)
        res.failure_class = cls.value
    elif not (res.stdout or "").strip() and (res.stderr or "").strip():
        # SUCESSO SILENCIOSO NÃO É SUCESSO. O AGY headless devolve exit 0 com
        # stdout vazio quando auto-nega uma ferramenta — o agente "passa" sem
        # ter feito nada e o Sprint não percebe. Exigimos saída útil.
        cls = errors.classificar(res.stderr, 1)
        res.failure_class = (cls.value if cls != errors.FailureClass.UNKNOWN
                             else errors.FailureClass.ENVIRONMENT_ERROR.value)
        res.stdout = res.stdout or ""
    _medir_tokens(res, log_path)
    _gravar_log(log_path, res)
    return res


def _fake(inv: Invocacao) -> Resultado:
    """Driver determinístico para testes — não gasta cota.

    Lê `fake_script` (JSON) descrevendo o comportamento esperado:
      {"acao":"editar","arquivo":"src/stats.py","conteudo":"..."}
      {"acao":"quota"}                     -> simula exaustão de cota do Codex
      {"acao":"quebrar"}                   -> deixa os testes falhando
      {"sequencia":[{...},{...}]}          -> comportamentos em ordem, por chamada

    A `sequencia` mantém um contador ao lado do arquivo de spec, para que a
    primeira chamada possa devolver cota e a segunda efetivamente implementar —
    é assim que o teste de retomada pós-cota prova "CONTINUAR, não reiniciar".
    """
    spec = {}
    if inv.fake_script and Path(inv.fake_script).exists():
        spec = json.loads(Path(inv.fake_script).read_text())

    def _conta() -> int:
        """Contador de invocações, persistido ao lado da spec."""
        if not inv.fake_script:
            return 0
        c = Path(inv.fake_script).with_suffix(".count")
        n = int(c.read_text().strip() or "0") if c.exists() else 0
        c.write_text(str(n + 1))
        return n

    if "sequencia" in spec:
        seq = spec["sequencia"]
        i = _conta()
        spec = seq[min(i, len(seq) - 1)]

    acao = spec.get("acao", "nada")
    resultado = _fake_acao(acao, spec, inv)
    # registra qual passo da sequência foi executado — a aceitação lê isso
    if inv.log_path:
        try:
            Path(inv.log_path).with_suffix(".acao").write_text(
                json.dumps({"acao": acao, "spec": spec}, ensure_ascii=False))
        except OSError:
            pass
    return resultado


def _fake_acao(acao: str, spec: dict, inv: Invocacao) -> Resultado:
    if acao == "quota":
        return Resultado(exit_code=1,
                         stdout="Error: quota exceeded for this billing period. "
                                "Your usage limit resets in 5 hours.",
                         failure_class=errors.FailureClass.CODEX_QUOTA.value)
    if acao == "editar":
        alvo = Path(inv.worktree) / spec["arquivo"]
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(spec["conteudo"], encoding="utf-8")
        return Resultado(exit_code=0, stdout=f"[fake] escreveu {spec['arquivo']}")
    if acao == "remover":
        alvo = Path(inv.worktree) / spec["arquivo"]
        if alvo.exists():
            alvo.unlink()
        return Resultado(exit_code=0, stdout=f"[fake] removeu {spec['arquivo']}")
    if acao == "quebrar":
        return Resultado(exit_code=0, stdout="[fake] deixou os testes falhando")
    if acao == "crash":
        return Resultado(exit_code=137, stderr="Killed",
                         failure_class=errors.FailureClass.AGENT_CRASH.value)
    if acao == "uso":
        # usado pelos testes para provar a fiacao tokens -> attempts sem gastar cota
        return Resultado(exit_code=0, stdout=spec.get("texto", "[fake] ok"),
                         tokens_total=spec.get("tokens"),
                         tokens_fonte="fake")
    if acao == "echo":
        return Resultado(exit_code=0, stdout=spec.get("texto", "[fake] ok"))
    return Resultado(exit_code=0, stdout="[fake] nada a fazer")


def _gravar_log(log_path: Path | None, res: Resultado) -> None:
    if not log_path:
        return
    res.log_path = str(log_path)
    log_path.write_text(
        f"# exit_code={res.exit_code} duracao={res.duracao:.1f}s "
        f"modelo={res.modelo_usado} effort={res.effort_usado} "
        f"failure_class={res.failure_class} "
        f"tokens={res.tokens_total if res.tokens_total is not None else 'nao medido'}"
        f"({res.tokens_fonte or '-'})\n"
        f"\n===== PROMPT ENVIADO =====\n{res.prompt}\n"
        f"\n===== STDOUT =====\n{res.stdout}\n"
        f"\n===== STDERR =====\n{res.stderr}\n", encoding="utf-8")
