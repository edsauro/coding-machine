"""DEVFACTORY — taxonomia de falhas (spec §20).

Toda falha é classificada ANTES de qualquer retry. A classe determina a estratégia:
escalonar modelo, esperar recurso, abrir HAQ ou bloquear. Um retry genérico é proibido.
"""
from __future__ import annotations

import datetime
import re
import time
from enum import Enum


class FailureClass(str, Enum):
    CODE_ERROR = "CODE_ERROR"
    TEST_FAILURE = "TEST_FAILURE"
    REVIEW_FAILURE = "REVIEW_FAILURE"
    DEPENDENCY_ERROR = "DEPENDENCY_ERROR"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    AGENT_CRASH = "AGENT_CRASH"
    CODEX_QUOTA = "CODEX_QUOTA"
    PERMISSION_REQUIRED = "PERMISSION_REQUIRED"
    SECRET_REQUIRED = "SECRET_REQUIRED"
    RED_ACTION_REQUIRED = "RED_ACTION_REQUIRED"
    # O agente respondeu sem entregar NADA: pergunta de design, pedido de
    # aprovação, plano — e nenhuma alteração no worktree. Achado real: em modo
    # headless não há humano para responder, então a tentativa é perdida; e a
    # suíte pré-existente continua verde, o que faria a revisão parecer "ok".
    SEM_ENTREGA = "SEM_ENTREGA"
    UNKNOWN = "UNKNOWN"


# Classes que exigem um humano (viram HAQ) ou uma espera de recurso — nunca
# escalonamento de modelo (spec §10).
CLASSES_HUMANAS = {
    FailureClass.PERMISSION_REQUIRED,
    FailureClass.SECRET_REQUIRED,
    FailureClass.RED_ACTION_REQUIRED,
}

CLASSES_DE_RECURSO = {FailureClass.CODEX_QUOTA}

CLASSES_AMBIENTE = {
    FailureClass.DEPENDENCY_ERROR,
    FailureClass.ENVIRONMENT_ERROR,
    FailureClass.NETWORK_ERROR,
}

# Estas consomem orçamento de tentativas de IMPLEMENTAÇÃO (spec §13).
CLASSES_QUE_CONSUMEM_TENTATIVA = {
    FailureClass.CODE_ERROR,
    FailureClass.TEST_FAILURE,
    FailureClass.REVIEW_FAILURE,
    FailureClass.AGENT_CRASH,
    FailureClass.SEM_ENTREGA,
    FailureClass.UNKNOWN,
}

SINAIS = {
    FailureClass.CODEX_QUOTA: [
        "quota exceeded", "usage limit", "rate limit", "rate-limit",
        "credits unavailable", "insufficient quota", "out of credits",
        "insufficient credit", "credits exhausted", "credit balance",
        "no credits remaining", "payment required",
        "limit resets", "too many requests", "429",
    ],
    FailureClass.PERMISSION_REQUIRED: [
        "permission denied", "operation not permitted", "eacces",
        "must be run as root", "requires sudo", "are you root",
    ],
    FailureClass.SECRET_REQUIRED: [
        "api key not set", "missing credentials", "unauthorized",
        "authentication failed", "no such file: .env", "env var not set",
    ],
    FailureClass.NETWORK_ERROR: [
        "could not resolve host", "connection refused", "network is unreachable",
        "timed out", "temporary failure in name resolution",
    ],
    FailureClass.DEPENDENCY_ERROR: [
        "modulenotfounderror", "no module named", "command not found",
        "cannot find package", "npm err! 404", "package not found",
    ],
    FailureClass.ENVIRONMENT_ERROR: [
        "no space left", "disk quota", "read-only file system",
        "too many open files", "broken pipe",
    ],
    FailureClass.TEST_FAILURE: [
        "assertionerror", "tests failed", "failed:", "1 failed", "failing",
    ],
    FailureClass.CODE_ERROR: [
        "syntaxerror", "traceback", "compilation error", "typeerror",
        "nameerror", "indentationerror",
    ],
}


def classificar(saida: str, exit_code: int | None = None,
                stderr: str = "") -> FailureClass:
    """Classifica uma falha a partir do texto do agente/teste.

    Ordem de precedência importa: cota > humano > rede > dependência > ambiente >
    teste > código. Cota vem primeiro porque consome o texto do Codex inteiro e
    não é falha de implementação (spec §12).
    """
    txt = f"{saida}\n{stderr}".lower()
    for classe in (FailureClass.CODEX_QUOTA, FailureClass.PERMISSION_REQUIRED,
                   FailureClass.SECRET_REQUIRED, FailureClass.NETWORK_ERROR,
                   FailureClass.DEPENDENCY_ERROR, FailureClass.ENVIRONMENT_ERROR,
                   FailureClass.TEST_FAILURE, FailureClass.CODE_ERROR):
        if any(s in txt for s in SINAIS[classe]):
            return classe
    if exit_code not in (None, 0):
        return FailureClass.UNKNOWN
    return FailureClass.UNKNOWN


def e_humana(c: FailureClass) -> bool:
    return c in CLASSES_HUMANAS


def e_recurso(c: FailureClass) -> bool:
    return c in CLASSES_DE_RECURSO


def consome_tentativa(c: FailureClass) -> bool:
    return c in CLASSES_QUE_CONSUMEM_TENTATIVA


# --------------------------------------------------------------- reset de cota
_MESES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
          "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DURACAO = re.compile(
    r"resets?\s+in\s+(?:(\d+)\s*h(?:ours?|rs?)?)?"
    r"(?:(\d+)\s*m(?:in(?:utes?)?)?)?(?:(\d+)\s*s(?:ec(?:onds?)?)?)?", re.I)
_ABSOLUTO_DATA = re.compile(
    r"try again at\s+([A-Za-z]{3})\w*\s+(\d{1,2})\w{0,2},?\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s*([AP])M", re.I)
_ABSOLUTO_HORA = re.compile(r"try again at\s+(\d{1,2}):(\d{2})\s*([AP])M", re.I)


def reset_de_cota(texto: str, agora: float | None = None) -> float | None:
    """Quando a cota volta, SEGUNDO O PRÓPRIO AGENTE. None se não der para ler.

    Duas formas reais, ambas observadas nesta madrugada:
      codex — "try again at 9:10 AM" / "try again at Sep 28th, 2026 3:41 AM"
      agy   — "Resets in 120h58m47s"

    Existe porque a política de 5h10m é chute: nesta mesma noite o codex avisou um
    reset de 31 min e o motor dormiu 5h por cima. O valor lido aqui é usado só
    quando é MENOR que a política — a política continua sendo o teto, para que um
    parse absurdo não estacione o sprint por dias.
    """
    agora = time.time() if agora is None else agora
    t = texto or ""

    m = _DURACAO.search(t)
    if m and any(g is not None for g in m.groups()):
        h, mi, s = (int(g or 0) for g in m.groups())
        return agora + h * 3600 + mi * 60 + s

    def _hora(h12: str, minuto: str, sufixo: str) -> int:
        h = int(h12) % 12
        if sufixo.upper() == "P":
            h += 12
        return h

    m = _ABSOLUTO_DATA.search(t)
    if m:
        mes = _MESES.get(m.group(1)[:3].lower())
        if mes:
            try:
                d = datetime.datetime(int(m.group(3)), mes, int(m.group(2)),
                                      _hora(m.group(4), m.group(5), m.group(6)),
                                      int(m.group(5)))
                return d.timestamp()
            except ValueError:
                return None

    m = _ABSOLUTO_HORA.search(t)
    if m:
        d = datetime.datetime.fromtimestamp(agora).replace(
            hour=_hora(m.group(1), m.group(2), m.group(3)), minute=int(m.group(2)),
            second=0, microsecond=0)
        ts = d.timestamp()
        if ts <= agora:          # 9:10 já passou hoje: o reset é amanhã
            ts += 86400
        return ts
    return None


def espera_efetiva(politica: int, texto: str, agora: float | None = None) -> int:
    """A espera a aplicar: o reset informado pelo agente quando ele é MENOR que a
    política. Sem reset legível — ou com reset maior/absurdo — vale a política, que
    é o teto. Nunca abaixo de 30s (evita laço apertado batendo na cota)."""
    agora = time.time() if agora is None else agora
    r = reset_de_cota(texto, agora)
    if not r:
        return politica
    delta = int(r - agora)
    return delta if 30 <= delta < politica else politica
