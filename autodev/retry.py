"""DEVFACTORY — lógica de retry, escalonamento e anti-loop (T10, spec §10/§11/§12/§13).

Duas contabilidades SEPARADAS:
  implementation_attempts  — tentativas de resolver o problema de código;
  resource_wait_attempts   — esperas de cota do Codex.

Espera de cota NÃO consome tentativa de implementação (spec §13).

A decisão nunca é "retry genérico": cada classe de falha tem uma estratégia.
"""
from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from enum import Enum

from . import errors


class Estrategia(str, Enum):
    RETRY_IGUAL = "RETRY"                     # tentativa 1 -> 2 com evidência
    ESCALONAR_MODELO = "ESCALATE_MODEL"
    TROCAR_AGENTE = "SWITCH_AGENT"
    BLOQUEAR = "BLOCK"
    ESPERAR_RECURSO = "WAIT_RESOURCE"
    ABRIR_HAQ = "OPEN_HAQ"
    PARAR = "STOP"


@dataclass
class Decisao:
    estrategia: Estrategia
    tentativa_proxima: int
    tier: int
    motivo: str
    failure_class: str
    retry_after: float | None = None
    agente: str | None = None
    fingerprint: str = ""
    requer_evidencia_nova: bool = True

    def to_dict(self) -> dict:
        return {"estrategia": self.estrategia.value,
                "tentativa_proxima": self.tentativa_proxima, "tier": self.tier,
                "motivo": self.motivo, "failure_class": self.failure_class,
                "retry_after": self.retry_after, "agente": self.agente,
                "fingerprint": self.fingerprint}


def fingerprint(task_id: str, failure_class: str, assinatura: str,
                arquivos: list[str] | None = None,
                testes_falhos: list[str] | None = None) -> str:
    """Impressão digital da falha (spec §11).

    Combina task, classe, assinatura normalizada do erro, arquivos afetados e
    testes falhos — para reconhecer "a mesma falha" entre tentativas.
    """
    partes = [task_id, failure_class, assinatura,
              ",".join(sorted(arquivos or [])),
              ",".join(sorted(testes_falhos or []))]
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:20]


def similaridade(fp_a: str, fp_b: str) -> float:
    """Similaridade entre duas impressões digitais (0..1)."""
    if not fp_a or not fp_b:
        return 0.0
    if fp_a == fp_b:
        return 1.0
    return difflib.SequenceMatcher(None, fp_a, fp_b).ratio()


def mesmo_lugar(fp_nova: str, historico: list[str], limiar: float = 0.85) -> bool:
    """True se a falha nova repete substancialmente uma anterior (loop)."""
    return any(similaridade(fp_nova, f) >= limiar for f in historico if f)


def decidir(*, failure_class: str, tentativas_implementacao: int,
            esperas_cota: int, agente_atual: str, cfg,
            fp_nova: str = "", fps_anteriores: list[str] | None = None,
            modo_teste: bool = False, agora: float | None = None) -> Decisao:
    """Decide a próxima ação. Sempre classifique antes de decidir (spec §20)."""
    import time as _t
    agora = agora if agora is not None else _t.time()
    fc = errors.FailureClass(failure_class)
    pol = cfg.policies["retry"]
    max_tent = pol["max_tentativas_implementacao"]
    sem_escalonar = cfg.classes_sem_escalonamento()
    fp = fp_nova or ""

    # --- cota do Codex: espera de recurso, nunca falha (spec §12) -------------
    if fc == errors.FailureClass.CODEX_QUOTA:
        espera = cfg.espera_cota(modo_teste)
        return Decisao(
            estrategia=Estrategia.ESPERAR_RECURSO,
            tentativa_proxima=tentativas_implementacao,   # NÃO incrementa
            tier=cfg.modelo_para_tentativa(max(tentativas_implementacao, 1))["tier"],
            motivo=f"cota do Codex esgotada; retomar em {espera}s "
                   f"(espera #{esperas_cota + 1})",
            failure_class=failure_class, retry_after=agora + espera,
            fingerprint=fp, requer_evidencia_nova=False)

    # --- falhas humanas viram HAQ e bloqueiam só a task dependente ------------
    if errors.e_humana(fc):
        return Decisao(estrategia=Estrategia.ABRIR_HAQ,
                       tentativa_proxima=tentativas_implementacao,
                       tier=0, motivo=f"exige intervencao humana: {fc.value}",
                       failure_class=failure_class, fingerprint=fp,
                       requer_evidencia_nova=False)

    # --- ambiente/dependência: corrigir o ambiente, não escalonar modelo ------
    if fc in errors.CLASSES_AMBIENTE:
        if tentativas_implementacao >= max_tent:
            return Decisao(Estrategia.BLOQUEAR, tentativas_implementacao, 0,
                           f"{fc.value} nao resolvido apos {tentativas_implementacao} "
                           f"tentativas", failure_class, fingerprint=fp)
        # P-09: o tier vai EXPLÍCITO (o degrau atual). Com 0, o orquestrador caía no
        # contador da task e a falha de rede pagava o próximo degrau da escada.
        return Decisao(Estrategia.RETRY_IGUAL, tentativas_implementacao + 1,
                       cfg.tier_atual(tentativas_implementacao),
                       f"{fc.value}: corrigir o ambiente antes de reexecutar "
                       f"(sem escalonar modelo)", failure_class, fingerprint=fp)

    # --- falha de código/teste/revisão: a escada da spec §10/§11 --------------
    prox = tentativas_implementacao + 1
    if prox > max_tent:
        return Decisao(Estrategia.BLOQUEAR, tentativas_implementacao, 0,
                       f"limite de {max_tent} tentativas de implementacao atingido",
                       failure_class, fingerprint=fp)
    if fc.value in sem_escalonar:
        # P-09: mesma regra do bloco de ambiente — tier explícito, degrau atual.
        return Decisao(Estrategia.RETRY_IGUAL, prox, cfg.tier_atual(tentativas_implementacao),
                       f"{fc.value} nao recebe escalonamento de modelo",
                       failure_class, fingerprint=fp)

    novo_tier = cfg.modelo_para_tentativa(prox)["tier"]
    tier_atual = cfg.modelo_para_tentativa(max(tentativas_implementacao, 1))["tier"]
    repetindo = mesmo_lugar(fp, fps_anteriores or [],
                            cfg.policies["fingerprint"]["limiar_similaridade"])

    troca_em = pol.get("troca_agente_na_tentativa", 4)
    if prox >= troca_em and repetindo:
        outro = "agy" if agente_atual == "codex" else "codex"
        return Decisao(Estrategia.TROCAR_AGENTE, prox, novo_tier,
                       f"mesma falha pela {prox}a vez — trocando para {outro}",
                       failure_class, agente=outro, fingerprint=fp)

    if novo_tier > tier_atual:
        m = cfg.modelo_para_tentativa(prox)
        return Decisao(Estrategia.ESCALONAR_MODELO, prox, novo_tier,
                       f"2 tentativas materialmente informadas falharam — "
                       f"escalonando para {m['slug']}/{m['effort']}",
                       failure_class, fingerprint=fp)
    return Decisao(Estrategia.RETRY_IGUAL, prox, novo_tier,
                   f"tentativa {prox}: reprocessar com a evidencia nova "
                   f"({fc.value})", failure_class, fingerprint=fp)


def montar_prompt_retry(prompt_original: str, *, decisao: Decisao,
                        saida_testes: str, arquivos: list[str],
                        tentativas_anteriores: list[dict]) -> str:
    """Retry SEMPRE inclui evidência nova (spec §10). Repetir o mesmo prompt é proibido."""
    hist = "\n".join(
        f"  - tentativa {t.get('attempt')}: {t.get('failure_class')} "
        f"(exit {t.get('exit_code')}) — {str(t.get('resumo',''))[:150]}"
        for t in tentativas_anteriores[-3:]) or "  (nenhuma)"
    # Falha explícita e dirigida para a tentativa que não entregou NADA: pedir
    # de novo o mesmo trabalho, sem reconhecer a pergunta, faz o agente repetir
    # a pergunta. Achado real (ver SEM_ENTREGA em errors.py).
    instrucao_extra = ""
    if getattr(decisao, "failure_class", "") == "SEM_ENTREGA":
        instrucao_extra = (
            "\nATENCAO: a tentativa anterior NAO alterou nenhum arquivo — o agente\n"
            "respondeu com uma pergunta de design/pedido de aprovacao. NAO ha humano\n"
            "disponivel para responder: perguntar conta como falha. Implemente agora,\n"
            "decida sozinho com o que a task pede, e deixe as mudancas no worktree.\n"
        )
    return f"""{prompt_original}

---
## CONTEXTO DAS TENTATIVAS ANTERIORES
{hist}

## EVIDENCIA NOVA (leia antes de agir)
### Saida do ultimo teste
```
{saida_testes[-4000:]}
```
### Arquivos ja tocados
{chr(10).join('  - ' + a for a in arquivos) or '  (nenhum)'}

## INSTRUCAO
Esta e a tentativa #{decisao.tentativa_proxima}.
Estrategia decidida: {decisao.estrategia.value}.
Motivo: {decisao.motivo}
{instrucao_extra}
NAO repita a abordagem anterior. Se ela falhou, explique em uma linha por que
falhou e adote uma abordagem diferente baseada na evidencia acima.
Se o problema for de ambiente/dependencia/permissao e nao de codigo, diga isso
explicitamente em vez de tentar contornar.
Responda ao final com: RESULTADO: <resumo do que mudou>
"""


def montar_prompt_continuacao(prompt_original: str, *, task_id: str,
                              criterios: list[str], estado_impl: str,
                              arquivos: list[str], ultimo_commit: str,
                              testes_passando: list[str],
                              testes_falhando: list[str],
                              tentativas: list[dict],
                              findings: list[dict],
                              proxima_acao: str) -> str:
    """Prompt de CONTINUAÇÃO após espera de cota (spec §12).

    Exige explicitamente continuar de onde parou, não recomeçar. Inclui o estado
    persistido — e só ele, não a conversa inteira.
    """
    def _l(xs, vazio="  (nenhum)"):
        return "\n".join(f"  - {x}" for x in xs) if xs else vazio
    hist = _l([f"tentativa {t.get('attempt')}: {t.get('failure_class','?')} — "
               f"{str(t.get('motivo',''))[:120]}" for t in tentativas[-5:]])
    return f"""{prompt_original}

---
# CONTINUACAO DE TASK EXISTENTE — NAO RECOMECE

Voce ja trabalhou nesta task antes e foi interrompido por esgotamento de cota.
O estado abaixo foi persistido. CONTINUE a partir dele.

## Task
{task_id}

## Criterios de aceitacao
{_l(criterios)}

## Estado atual da implementacao
{estado_impl}

## Arquivos relevantes
{_l(arquivos)}

## Ultimo commit
{ultimo_commit}

## Testes que JA PASSAM (nao mexa sem motivo)
{_l(testes_passando)}

## Testes que AINDA FALHAM
{_l(testes_falhando)}

## Tentativas anteriores
{hist}

## Findings de revisao em aberto
{_l([f"[{f.get('severidade')}] {f.get('arquivo')}: {f.get('descricao')}"
     for f in findings])}

## PROXIMA ACAO ESPERADA
{proxima_acao}

## Regras
1. NAO reescreva o que ja funciona.
2. NAO recomece a task do zero.
3. Faca a menor mudanca que resolve o proximo item em aberto.
4. Ao terminar, responda com: RESULTADO: <o que mudou>
"""
