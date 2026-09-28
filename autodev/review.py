"""DEVFACTORY — revisor independente (T09, spec §16).

Regra: quem implementa NÃO é a autoridade que decide que terminou.
  codex implementa -> agy revisa
  agy   implementa -> codex revisa
  revisor cruzado indisponível -> testes determinísticos + revisão Hermes/DeepSeek

Quem revisa cada tentativa vem da ESCADA DE REVISÃO (models.yaml, matriz do autor
de 2026-09-27): 1-3 = AGY Gemini 3.6/3.7/3.8 Flash; 4-5 = Hermes DeepSeek
flash/pro. Sem escada configurada vale a revisão cruzada. Quando o revisor da
tentativa não pode rodar (cota do AGY esgotada, wrapper ausente, saída ilegível),
entra o revisor de RESERVA — a troca fica registrada em `origem`, nunca em
silêncio, e jamais vira "aprovado por padrão".

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

# Instrução extra quando quem revisa é o Hermes headless (que tem ferramentas).
PROMPT_SO_LEITURA = """

## Importante
Voce esta em modo SOMENTE LEITURA: nao edite, crie nem remova arquivo nenhum.
Sua resposta e o produto: um unico bloco JSON, nada antes e nada depois dele.
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
    origem: str = ""            # de onde veio o revisor: escada/cruzada/reserva
    sem_cota: list[str] = field(default_factory=list)   # agentes que estouraram cota

    @property
    def aprovado(self) -> bool:
        return self.veredito == "APPROVE"

    def to_dict(self) -> dict:
        return {"veredito": self.veredito, "revisor": self.revisor,
                "modelo": self.modelo, "confianca": self.confianca,
                "findings": self.findings, "criterios": self.criterios,
                "resumo": self.resumo, "erro": self.erro, "origem": self.origem,
                "sem_cota": self.sem_cota}


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


def _disponivel(nome: str, disponiveis: dict) -> bool:
    """True quando dá para TENTAR o agente (ausência de registro = não sei, tenta)."""
    info = disponiveis.get(nome)
    return True if info is None else bool(info.disponivel)


def _escolhe_revisor(cfg, agente_impl: str, disponiveis: dict,
                     tentativa: int | None,
                     modelo_revisor: str | None) -> tuple[str, str | None, str]:
    """(agente, modelo, origem) do revisor desta tentativa."""
    if tentativa:
        try:
            spec = cfg.revisor_para_tentativa(tentativa)
        except (AttributeError, TypeError):
            spec = {}
        if spec:
            ag = spec.get("agente") or "hermes"
            if _disponivel(ag, disponiveis):
                return ag, spec.get("modelo"), f"escada (tentativa {tentativa})"
            res = cfg.revisor_reserva() if hasattr(cfg, "revisor_reserva") else {}
            if res:
                return (res.get("agente") or "hermes", res.get("modelo"),
                        f"reserva (revisor da escada '{ag}' indisponivel)")
    return escolher_revisor(agente_impl, disponiveis), modelo_revisor, "cruzada"


def _revisar_por_llm(agente: str, modelo: str | None, prompt: str, worktree: str,
                     cfg, log_dir: str | None,
                     task_id: str) -> tuple[Revisao | None, str]:
    """Invoca um revisor LLM. Devolve (Revisao, "") ou (None, motivo da falha)."""
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    inv = Invocacao(agente=agente, prompt=prompt, worktree=worktree,
                    modelo=modelo, effort=None, edita=False, timeout=900,
                    log_path=str(Path(log_dir) / f"review-{task_id}.log")
                    if log_dir else None)
    res = invocar(inv, cfg)
    if not res.ok:
        return None, f"{res.failure_class or res.exit_code}"
    dados = _extrai_json(res.stdout)
    if not dados or dados.get("veredito") not in VEREDITOS:
        return None, "revisor nao devolveu veredito parseavel"
    r = Revisao(veredito=dados["veredito"], revisor=agente,
                modelo=res.modelo_usado or (modelo or ""),
                confianca=float(dados.get("confianca", 0)),
                findings=dados.get("findings", []),
                criterios=dados.get("criterios_atendidos", []),
                resumo=dados.get("resumo", ""), cru=res.stdout[:4000])
    return r, ""


def _aplica_piso(r: Revisao, worktree: str, base: str, criterios: list[str],
                 testes: str) -> Revisao:
    """Portão objetivo como PISO do veredito.

    O revisor LLM não aprova o que as checagens objetivas reprovam: segredo no
    diff, comando destrutivo ou worktree sem alteração nenhuma. A divergência não
    é escondida — os findings do piso entram no resultado.
    """
    if not r.aprovado:
        return r
    piso = _revisao_deterministica(worktree, base, criterios, testes,
                                   motivo="piso deterministico")
    altas = [f for f in piso.findings if f.get("severidade") == "alta"]
    if not altas:
        return r
    r.findings = list(r.findings) + altas
    r.veredito = "REQUEST_CHANGES"
    r.resumo = (f"{r.resumo} | piso deterministico reprovou: "
                f"{'; '.join(f['descricao'] for f in altas)}")[:400]
    return r


def revisar(*, worktree: str, base: str, task_id: str, titulo: str,
            criterios: list[str], testes: str, agente_impl: str,
            cfg, disponiveis: dict, log_dir: str | None = None,
            arquitetura: str = "simplicidade > confiabilidade > recuperacao > "
                               "verificacao deterministica > seguranca",
            seguranca: str = "sem sudo/root, sem credenciais, sem escrita fora do repo",
            modelo_revisor: str | None = None,
            tentativa: int | None = None) -> Revisao:
    revisor, modelo_revisor, origem = _escolhe_revisor(
        cfg, agente_impl, disponiveis, tentativa, modelo_revisor)
    prompt = PROMPT.format(
        criterios="\n".join(f"- {c}" for c in criterios),
        task_id=task_id, titulo=titulo, base=base,
        diff=_diff(worktree, base), testes=testes or "(sem saida)",
        arquitetura=arquitetura, seguranca=seguranca)

    # Revisão determinística local (sem LLM): caminho antigo, quando o revisor
    # cruzado não existe e não há escada apontando outro revisor.
    if revisor == "hermes" and not origem.startswith(("escada", "reserva")):
        r = _revisao_deterministica(worktree, base, criterios, testes,
                                    motivo="revisor cruzado indisponivel")
        r.origem = origem
        return r

    if revisor == "hermes":
        prompt += PROMPT_SO_LEITURA

    r, motivo = _revisar_por_llm(revisor, modelo_revisor, prompt, worktree, cfg,
                                 log_dir, task_id)
    if r is not None:
        r.origem = origem
        return _aplica_piso(r, worktree, base, criterios, testes)

    # --- revisor da tentativa falhou: percorre a CADEIA de reserva antes de cair
    # no portão determinístico. Regra do autor (2026-09-27): o revisor nunca
    # interrompe o sprint — troca de modelo é preferível a parar.
    motivos = [f"{revisor}: {motivo}"]
    # Quem estourou cota não é reescolhido nesta rodada — o orquestrador recebe
    # esta lista e tira o agente de circulação até a cota voltar (D-15).
    sem_cota = [revisor] if "CODEX_QUOTA" in motivo else []
    if tentativa and hasattr(cfg, "revisor_reserva_cadeia"):
        for res in cfg.revisor_reserva_cadeia():
            ag_res = res.get("agente") or "hermes"
            prompt_res = prompt + (PROMPT_SO_LEITURA if ag_res == "hermes" else "")
            r2, motivo2 = _revisar_por_llm(ag_res, res.get("modelo"), prompt_res,
                                           worktree, cfg, log_dir, task_id)
            if r2 is not None:
                r2.origem = (f"reserva ({ag_res}/{res.get('modelo')}) — "
                             f"falhou antes: {'; '.join(motivos)}")
                r2.sem_cota = sem_cota
                return _aplica_piso(r2, worktree, base, criterios, testes)
            motivos.append(f"{ag_res}/{res.get('modelo')}: {motivo2}")
            if "CODEX_QUOTA" in motivo2 and ag_res not in sem_cota:
                sem_cota.append(ag_res)

    r = _revisao_deterministica(worktree, base, criterios, testes,
                                motivo="revisores LLM esgotados — "
                                       + "; ".join(motivos))
    r.origem = origem
    r.sem_cota = sem_cota
    return r


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
