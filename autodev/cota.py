"""Leitura das janelas de cota do Codex — para a espera mirar a janela certa (P-14).

Por que existe: quando o Codex recusa por cota, o motor gravava um prazo FIXO de 5h10m
(``retry.espera_cota_s``). Em 29/09/2026 01:50 a janela de 5h estava em ~30% e quem havia
estourado era a **semanal** (97%): a espera apontou para 07:00 e o motor ia acordar, falhar
e re-esperar 5h10m ~19 vezes até o reset real (04/10 00:22). Sem gastar token — a recusa é
do CLI, antes do modelo — mas acordando o vigia a cada ciclo e publicando uma previsão de
retomada errada no log e no ``status``.

O Codex expõe as duas janelas por JSON-RPC no próprio app-server
(``account/rateLimits/read``): ``primary`` = janela de 5h, ``secondary`` = 7 dias, cada uma
com ``usedPercent`` e ``resetsAt``. É a mesma leitura que o diagnóstico manual usa.

Regra de escolha: das janelas acima do piso, a que está MAIS cheia é a que estourou — é o
``resetsAt`` dela que vale. Nada acima do piso e nenhuma espera é sugerida: aí o motor cai
no prazo da política, que é o comportamento antigo (e correto, quando o motivo é outro).
"""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time

# Piso de "estourou": o medidor é inteiro e a recusa chega antes de marcar 100 (em 29/09 a
# semanal recusou com 97). Abaixo disto não se afirma qual janela é a culpada.
PISO_PERCENTUAL = 90
MARGEM_S = 120.0          # folga para o reset do lado do servidor
TIMEOUT_RPC_S = 25.0


def _ler_rate_limits(timeout_s: float = TIMEOUT_RPC_S) -> dict | None:
    """``rateLimits`` do app-server do Codex, ou None se a leitura falhar.

    Nunca levanta: cota indisponível não pode derrubar o motor, só faz a espera voltar ao
    prazo da política.
    """
    try:
        p = subprocess.Popen(["codex", "app-server", "--listen", "stdio://"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except Exception:
        return None
    fila: queue.Queue = queue.Queue()

    def leitor() -> None:
        for linha in p.stdout or []:
            linha = linha.strip()
            if linha.startswith("{"):
                try:
                    fila.put(json.loads(linha))
                except ValueError:
                    pass

    threading.Thread(target=leitor, daemon=True).start()

    def envia(obj: dict) -> None:
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()

    def espera_resposta(id_: int) -> dict | None:
        fim = time.time() + timeout_s
        while time.time() < fim:
            try:
                m = fila.get(timeout=1)
            except queue.Empty:
                continue
            if m.get("id") == id_:
                return m
        return None

    try:
        envia({"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"clientInfo": {"name": "autodev", "title": "autodev",
                                         "version": "1.0"}}})
        espera_resposta(1)
        envia({"jsonrpc": "2.0", "id": 2, "method": "account/rateLimits/read",
               "params": {"supportsLunaReserve": True,
                          "excludeResetCreditDetails": True}})
        r = espera_resposta(2)
        if r and isinstance(r.get("result"), dict):
            return r["result"].get("rateLimits") or r["result"]
        return None
    except Exception:
        return None
    finally:
        try:
            p.terminate()
        except Exception:
            pass


def janela_estourada(rate_limits: dict | None,
                     piso: float = PISO_PERCENTUAL) -> dict | None:
    """A janela mais cheia acima do piso, ou None. Função pura (testável sem RPC)."""
    if not rate_limits:
        return None
    candidatas = []
    for nome in ("primary", "secondary"):
        w = rate_limits.get(nome) or {}
        pct = w.get("usedPercent")
        if pct is None or w.get("resetsAt") is None:
            continue
        candidatas.append({"nome": nome, "usedPercent": pct,
                           "resetsAt": w["resetsAt"],
                           "windowDurationMins": w.get("windowDurationMins")})
    if not candidatas:
        return None
    cheia = max(candidatas, key=lambda c: c["usedPercent"])
    return cheia if cheia["usedPercent"] >= piso else None


def _rotulo(janela: dict) -> str:
    minutos = janela.get("windowDurationMins") or 0
    if minutos >= 1440:
        return f"janela de {minutos // 1440}d ({janela['usedPercent']}%)"
    if minutos >= 60:
        return f"janela de {minutos // 60}h ({janela['usedPercent']}%)"
    return f"janela de {minutos}min ({janela['usedPercent']}%)"


def espera_sugerida(rate_limits: dict | None = None, agora: float | None = None,
                    margem_s: float = MARGEM_S, piso: float = PISO_PERCENTUAL
                    ) -> tuple[float, str] | None:
    """Espera até o reset da janela que estourou: ``(segundos, motivo)`` ou None.

    ``rate_limits`` None faz a leitura de verdade (RPC). Separado assim para o teste
    injetar as janelas sem tocar a rede.
    """
    if rate_limits is None:
        rate_limits = _ler_rate_limits()
    janela = janela_estourada(rate_limits, piso=piso)
    if not janela:
        return None
    agora = agora if agora is not None else time.time()
    falta = float(janela["resetsAt"]) - agora + margem_s
    if falta <= 0:
        # reset já passou (ou o relógio do servidor está atrás): espera curta e nova leitura
        return None
    return falta, f"cota do Codex esgotada — {_rotulo(janela)}, retomada no reset"
