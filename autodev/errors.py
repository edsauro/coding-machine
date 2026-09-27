"""DEVFACTORY — taxonomia de falhas (spec §20).

Toda falha é classificada ANTES de qualquer retry. A classe determina a estratégia:
escalonar modelo, esperar recurso, abrir HAQ ou bloquear. Um retry genérico é proibido.
"""
from __future__ import annotations

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
