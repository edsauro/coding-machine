"""DEVFACTORY — revisor independente (T09, spec §16).

Regra: quem implementa NÃO é a autoridade que decide que terminou.
  codex implementa -> agy revisa
  agy   implementa -> codex revisa
  revisor cruzado indisponível -> testes determinísticos + revisão Hermes/DeepSeek

O revisor recebe: critérios de aceitação do sprint, o diff, a saída dos testes,
as restrições arquiteturais e a política de segurança. Devolve um veredito
estruturado. Nunca revisa o próprio trabalho.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .agents import Invocacao, invocar
from .worktree import git

VEREDITOS = ("APPROVE", "REQUEST_CHANGES", "REJECT")

PROMPT = """Voce e o REVISOR INDEPENDENTE de uma task de implementacao.
Voce NAO escreveu este codigo. Seu trabalho e tentar reprovar, nao aprovar.

## Criterios de aceitacao do Sprint
{criterios}

## Task
{task_id}: {titulo}

## Diff a revisar (base {base} -> HEAD)
```diff
{diff}
```

## Saida dos testes
```
{testes}
```

## Restricoes de arquitetura
{arquitetura}

## Politica de seguranca
{seguranca}

## O que voce DEVE verificar
1. O codigo atende TODOS os criterios de aceitacao? Cite cada um.
2. O diff contem algo que os testes NAO cobrem?
3. Ha segredo/credencial, caminho absoluto fragil, ou escrita fora do projeto?
4. Ha dependencia nova? Ela e justificada?
5. Ha codigo morto, TODOs ou implementacao pela metade?
6. O teste realmente prova o comportamento, ou apenas passa?

## Formato da resposta (obrigatorio)
Responda com um unico bloco JSON, nada antes e nada depois:
```json
{{
  "veredito": "APPROVE" | "REQUEST_CHANGES" | "REJECT",
  "confianca": 0.0,
  "criterios_atendidos": [{{"criterio": "...", "atende": true, "evidencia": "..."}}],
  "findings": [{{"severidade": "alta|media|baixa", "arquivo": "...", "linha": 0, "descricao": "...", "correcao_sugerida": "..."}}],
  "resumo": "uma frase"
}}
```
"""


@dataclass
class Revisao:
    veredito: str
    revisor: str
    modelo: str = ""
    confianca: float = 0.0
    findings: list[dict] = field(default_factory=list)
    criterios: list[dict] = field(default_factory=list)
    resumo: str = ""
    cru: str = ""
    erro: str = ""

    @property
    def aprovado(self) -> bool:
        return self.veredito == "APPROVE"

    def to_dict(self) -> dict:
        return {"veredito": self.veredito, "revisor": self.revisor,
                "modelo": self.modelo, "confianca": self.confianca,
                "findings": self.findings, "criterios": self.criterios,
                "resumo": self.resumo, "erro": self.erro}


def _extrai_json(txt: str) -> dict | None:
    """Extrai o bloco JSON do texto do revisor, tolerando cercas markdown."""
    for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.S):
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
    m = re.search(r"\{.*\}", txt, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _diff(worktree: str, base: str, limite: int = 24000) -> str:
    d = git("diff", f"{base}..HEAD", cwd=worktree, check=False)
    if not d:
        d = git("diff", cwd=worktree, check=False)
    if len(d) > limite:
        d = d[:limite] + f"\n... [diff truncado em {limite} chars]"
    return d or "(sem alteracoes)"


def escolher_revisor(agente_impl: str, disponiveis: dict) -> str:
    """Revisão cruzada (spec §16). Cai para hermes quando o cruzado não existe."""
    cruzado = "agy" if agente_impl == "codex" else "codex"
    if disponiveis.get(cruzado) and disponiveis[cruzado].disponivel:
        return cruzado
    return "hermes"


def revisar(*, worktree: str, base: str, task_id: str, titulo: str,
            criterios: list[str], testes: str, agente_impl: str,
            cfg, disponiveis: dict, log_dir: str | None = None,
            arquitetura: str = "simplicidade > confiabilidade > recuperacao > "
                               "verificacao deterministica > seguranca",
            seguranca: str = "sem sudo/root, sem credenciais, sem escrita fora do repo",
            modelo_revisor: str | None = None) -> Revisao:
    revisor = escolher_revisor(agente_impl, disponiveis)
    prompt = PROMPT.format(
        criterios="\n".join(f"- {c}" for c in criterios),
        task_id=task_id, titulo=titulo, base=base,
        diff=_diff(worktree, base), testes=testes or "(sem saida)",
        arquitetura=arquitetura, seguranca=seguranca)

    if revisor == "hermes":
        # Revisão determinística local: sem LLM. Aplica checagens objetivas e
        # só então delega ao orquestrador. Mantém o Sprint andando quando o
        # revisor cruzado não existe (spec §16).
        return _revisao_deterministica(worktree, base, criterios, testes,
                                       motivo="revisor cruzado indisponivel")

    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    inv = Invocacao(agente=revisor, prompt=prompt, worktree=worktree,
                    modelo=modelo_revisor, effort=None, edita=False, timeout=900,
                    log_path=str(Path(log_dir) / f"review-{task_id}.log") if log_dir else None)
    res = invocar(inv, cfg)
    if not res.ok:
        r = _revisao_deterministica(worktree, base, criterios, testes,
                                    motivo=f"revisor {revisor} falhou: "
                                           f"{res.failure_class or res.exit_code}")
        r.cru = res.stdout[:4000]
        r.erro = res.stderr[:1000]
        return r

    dados = _extrai_json(res.stdout)
    if not dados or dados.get("veredito") not in VEREDITOS:
        r = _revisao_deterministica(worktree, base, criterios, testes,
                                    motivo="revisor nao devolveu veredito parseavel")
        r.cru = res.stdout[:4000]
        return r
    return Revisao(veredito=dados["veredito"], revisor=revisor,
                   modelo=res.modelo_usado, confianca=float(dados.get("confianca", 0)),
                   findings=dados.get("findings", []),
                   criterios=dados.get("criterios_atendidos", []),
                   resumo=dados.get("resumo", ""), cru=res.stdout[:4000])


def _revisao_deterministica(worktree: str, base: str, criterios: list[str],
                            testes: str, motivo: str) -> Revisao:
    """Portões objetivos — roda sempre, com ou sem LLM revisor."""
    findings: list[dict] = []
    diff = _diff(worktree, base)

    padroes_risco = [
        (r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}", "alta",
         "possivel segredo embutido no codigo"),
        (r"(?i)BEGIN (RSA|OPENSSH|PRIVATE) KEY", "alta", "chave privada no diff"),
        (r"(?m)^\+\s*.*\b(sudo|rm\s+-rf\s+/)\b", "alta", "comando privilegiado/destrutivo"),
        (r"(?i)subprocess\.\w+\([^)]*shell\s*=\s*True", "media", "shell=True sem necessidade"),
        (r"(?m)^\+\s*(TODO|FIXME|XXX)\b", "baixa", "marcador de pendencia no codigo"),
    ]
    for rx, sev, desc in padroes_risco:
        if re.search(rx, diff):
            findings.append({"severidade": sev, "arquivo": "(diff)",
                             "linha": 0, "descricao": desc,
                             "correcao_sugerida": "remover antes de integrar"})
    if "(sem alteracoes)" in diff:
        findings.append({"severidade": "alta", "arquivo": "(diff)", "linha": 0,
                         "descricao": "nenhuma alteracao encontrada no worktree",
                         "correcao_sugerida": "a task nao produziu codigo"})
    if "Traceback" in testes or "ERROR" in testes.upper():
        findings.append({"severidade": "media", "arquivo": "(testes)", "linha": 0,
                         "descricao": "saida dos testes contem erro",
                         "correcao_sugerida": "corrigir antes de integrar"})

    alta = [f for f in findings if f["severidade"] == "alta"]
    veredito = "REQUEST_CHANGES" if alta else "APPROVE"
    return Revisao(veredito=veredito, revisor="hermes-deterministico",
                   confianca=0.5, findings=findings,
                   resumo=f"{motivo}; {len(findings)} finding(s), {len(alta)} de severidade alta",
                   criterios=[{"criterio": c, "atende": None,
                               "evidencia": "verificacao humana/LLM pendente"}
                              for c in criterios])
