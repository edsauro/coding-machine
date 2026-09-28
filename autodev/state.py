"""DEVFACTORY — store de estado persistente em SQLite (T03, spec §4/§7/§22).

Esta é a fonte autoritativa do Sprint, junto com Git e os arquivos do sprint.
Memória conversacional NUNCA é usada como estado (spec §7).

Todas as operações são idempotentes: reexecutar após crash não duplica nada
(spec §22). O schema usa CREATE TABLE IF NOT EXISTS e INSERT OR IGNORE onde faz
sentido.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import (ESTADOS, ORDEM_SPRINT, SPRINT_TERMINAIS, TERMINAIS_TASK,
                     TRANSICOES, TRANSICOES_SPRINT)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- tarefas
CREATE TABLE IF NOT EXISTS tasks (
    sprint_id     TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    titulo        TEXT,
    estado        TEXT NOT NULL DEFAULT 'NEW',
    agente        TEXT,                 -- dono atual (um único escritor)
    worktree      TEXT,
    branch        TEXT,
    tentativas    INTEGER NOT NULL DEFAULT 0,   -- tentativas de IMPLEMENTACAO
    esperas_cota  INTEGER NOT NULL DEFAULT 0,   -- tentativas de ESPERA DE RECURSO
    tier_atual    INTEGER NOT NULL DEFAULT 0,
    fingerprint   TEXT,
    bloqueio      TEXT,
    criado_em     REAL NOT NULL,
    atualizado_em REAL NOT NULL,
    PRIMARY KEY (sprint_id, task_id)
);

-- ---------------------------------------------------------------- tentativas
CREATE TABLE IF NOT EXISTS attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id      TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    attempt        INTEGER NOT NULL,
    agent          TEXT,
    model          TEXT,
    effort         TEXT,
    branch         TEXT,
    worktree       TEXT,
    sandbox        TEXT,
    base_commit    TEXT,
    start_time     REAL,
    last_heartbeat REAL,
    end_time       REAL,
    status         TEXT,                -- RUNNING | OK | FAILED | WAITING_RESOURCE
    exit_code      INTEGER,
    changed_files  TEXT,                -- JSON list
    test_result    TEXT,                -- JSON
    review_result  TEXT,                -- JSON
    final_commit   TEXT,
    failure_class  TEXT,
    fingerprint    TEXT,
    retry_after    REAL,
    log_path       TEXT,
    origem         TEXT,                -- orquestrador | retroativo
    UNIQUE (sprint_id, task_id, attempt)
);

-- ---------------------------------------------------------------- eventos
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    sprint_id TEXT,
    task_id   TEXT,
    tipo      TEXT NOT NULL,
    payload   TEXT                      -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_sprint ON events(sprint_id, ts);

-- ---------------------------------------------------------------- HAQ
CREATE TABLE IF NOT EXISTS haq (
    haq_id      TEXT PRIMARY KEY,
    sprint_id   TEXT,
    task_id     TEXT,
    reason      TEXT,
    risk        TEXT,
    dependencia TEXT,
    acao_humana TEXT,
    resultado   TEXT,
    verificacao TEXT,
    criado_em   REAL,
    status      TEXT DEFAULT 'OPEN',    -- OPEN | DONE
    failure_class TEXT                  -- classe que originou o item (spec §20)
);

-- ---------------------------------------------------------------- kill switch
CREATE TABLE IF NOT EXISTS killswitch (
    nivel     TEXT PRIMARY KEY,         -- STOP_ALL | STOP_PROJECT | STOP_SPRINT | STOP_TASK
    alvo      TEXT,                     -- project/sprint/task id
    ativo     INTEGER NOT NULL DEFAULT 0,
    motivo    TEXT,
    ts        REAL
);

-- ---------------------------------------------------------------- esperas de cota
CREATE TABLE IF NOT EXISTS resource_waits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id    TEXT, task_id TEXT, agent TEXT,
    detectado_em REAL,
    retry_after  REAL,
    motivo       TEXT,
    resolvido    INTEGER DEFAULT 0
);

-- ---------------------------------------------------------------- lock de escritor
-- Garante um único escritor por worktree (spec §15/§21), mesmo entre processos.
CREATE TABLE IF NOT EXISTS writer_lock (
    worktree   TEXT PRIMARY KEY,
    task_id    TEXT,
    agent      TEXT,
    pid        INTEGER,
    heartbeat  REAL
);

-- ---------------------------------------------------------------- rodada única por sprint
-- O motor é quem garante que NÃO existe rodada dupla no mesmo sprint. Depender de
-- heurística de fora (guardar por pgrep, ou por heartbeat do writer_lock, que não é
-- reescrito durante uma revisão de 5 min) já custou caro: em 28/09 uma segunda rodada
-- disparada por vigia integrou uma task no meio da revisão de outra e a bloqueou por
-- limite de tentativas, derrubando o sprint com TransicaoInvalida.
CREATE TABLE IF NOT EXISTS run_lock (
    sprint_id   TEXT PRIMARY KEY,
    pid         INTEGER,
    iniciado_em REAL,
    heartbeat   REAL,
    nota        TEXT
);

-- ---------------------------------------------------------------- checkpoint do sprint
CREATE TABLE IF NOT EXISTS sprint_state (
    sprint_id    TEXT PRIMARY KEY,
    estado       TEXT,
    checkpoint   TEXT,                  -- JSON: o que estava acontecendo
    ultima_onda  INTEGER,
    atualizado_em REAL
);

-- Histórico append-only de checkpoints. `sprint_state` guarda só o estado ATUAL;
-- sem este histórico, uma retomada de cota sobrescreveria o registro do momento
-- em que a espera começou e a auditoria da retomada se perderia.
CREATE TABLE IF NOT EXISTS checkpoints (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sprint_id    TEXT NOT NULL,
    estado       TEXT,
    checkpoint   TEXT,
    onda         INTEGER,
    criado_em    REAL
);
CREATE INDEX IF NOT EXISTS idx_ckpt_sprint ON checkpoints(sprint_id, criado_em);
"""


class SchemaDesatualizado(Exception):
    """O banco em disco tem uma estrutura que a reconciliação não conseguiu
    alinhar com o SCHEMA do código (ex.: coluna de PRIMARY KEY faltando, que o
    SQLite não permite adicionar com ALTER TABLE)."""


def _divide_colunas(corpo: str) -> list[str]:
    """Divide por vírgula de primeiro nível (fora de parênteses e de aspas).

    Existe porque o DDL pode declarar várias colunas na MESMA linha
    (`sprint_id TEXT, task_id TEXT, agent TEXT`), e um parser linha-a-linha
    trataria isso como uma coluna só.
    """
    partes: list[str] = []
    buf: list[str] = []
    prof = 0
    aspas = ""
    for ch in corpo:
        if aspas:
            if ch == aspas:
                aspas = ""
            buf.append(ch)
        elif ch in "'\"":
            aspas = ch
            buf.append(ch)
        elif ch == "(":
            prof += 1
            buf.append(ch)
        elif ch == ")":
            prof -= 1
            buf.append(ch)
        elif ch == "," and prof == 0:
            partes.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        partes.append("".join(buf))
    return partes


def _colunas_declaradas(schema: str = SCHEMA) -> dict[str, list[tuple[str, str]]]:
    """Extrai {tabela: [(coluna, tipo), ...]} do próprio DDL.

    Usado pela reconciliação de schema. Ler o DDL em vez de manter uma segunda
    lista à mão é o que evita a lista envelhecer em silêncio.
    """
    tabelas: dict[str, list[tuple[str, str]]] = {}
    atual = ""          # "" = fora de um bloco CREATE TABLE
    for linha in schema.splitlines():
        s = linha.strip()
        m = re.match(r"CREATE TABLE IF NOT EXISTS\s+(\w+)\s*\(", s)
        if m:
            atual = m.group(1)
            tabelas[atual] = []
            continue
        if not atual:
            continue
        if s.startswith(")"):
            atual = ""
            continue
        if not s or s.startswith("--"):
            continue
        for pedaco in _divide_colunas(s.split("--")[0]):
            corpo = pedaco.strip().rstrip(",").strip()
            if not corpo or corpo.upper().startswith(
                    ("PRIMARY KEY", "UNIQUE", "FOREIGN KEY", "CHECK", "CONSTRAINT")):
                continue
            partes = corpo.split(None, 1)
            if len(partes) == 2:
                tabelas[atual].append((partes[0], partes[1].strip()))
    return tabelas


class TransicaoInvalida(Exception):
    pass


class SprintNaoEncerravel(Exception):
    """Tentativa de encerrar um sprint com task fora de estado terminal."""


class WorktreeOcupado(Exception):
    pass


class StateStore:
    def __init__(self, caminho: str | Path):
        self.caminho = Path(caminho)
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.caminho), timeout=30,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrar()

    def _migrar(self) -> None:
        """Reconcilia o schema do código com o banco já em disco.

        O SCHEMA usa CREATE TABLE IF NOT EXISTS, então coluna nova em tabela que
        JÁ existe não aparece sozinha: o banco fica silenciosamente atrás do
        código, e o erro só aparece quando alguém usa a coluna nova (foi assim
        que `haq.failure_class` quebrou num banco real).

        A reconciliação é genérica de propósito: as colunas são lidas do próprio
        SCHEMA. Uma segunda lista escrita à mão envelheceria exatamente como o
        schema velho que causou o problema.
        """
        faltando: list[str] = []
        for tabela, colunas in _colunas_declaradas().items():
            existentes = {r["name"] for r in
                          self.conn.execute(f"PRAGMA table_info({tabela})")}
            if not existentes:
                continue      # tabela ainda não existe: o SCHEMA acabou de criá-la
            for nome, tipo in colunas:
                if nome in existentes:
                    continue
                if "PRIMARY KEY" in tipo.upper():
                    faltando.append(f"{tabela}.{nome} (PRIMARY KEY: exige rebuild)")
                    continue
                try:
                    self.conn.execute(
                        f"ALTER TABLE {tabela} ADD COLUMN {nome} {tipo}")
                except sqlite3.OperationalError as e:
                    faltando.append(f"{tabela}.{nome}: {e}")
        if faltando:
            raise SchemaDesatualizado(
                "não foi possível reconciliar o schema: " + "; ".join(faltando))
        # dado derivado da migração: tentativas antigas são do orquestrador
        self.conn.execute(
            "UPDATE attempts SET origem='orquestrador' WHERE origem IS NULL")

    # ---------------------------------------------------------------- infra
    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    # ---------------------------------------------------------------- tarefas
    def criar_task(self, sprint_id: str, task_id: str, titulo: str = "",
                   agente: str | None = None) -> None:
        """Idempotente: reexecutar não sobrescreve estado existente."""
        agora = time.time()
        self.conn.execute(
            "INSERT OR IGNORE INTO tasks (sprint_id, task_id, titulo, estado, agente,"
            " criado_em, atualizado_em) VALUES (?,?,?,'NEW',?,?,?)",
            (sprint_id, task_id, titulo, agente, agora, agora))

    def criar_tasks_do_dag(self, sprint_id: str, dag: dict) -> int:
        n = 0
        for t in dag["tasks"]:
            antes = self.conn.total_changes
            self.criar_task(sprint_id, t["id"], t.get("titulo", ""), t.get("agente"))
            if self.conn.total_changes > antes:
                n += 1
        self.evento(sprint_id, None, "dag_carregado",
                    {"tasks": len(dag["tasks"]), "novas": n})
        return n

    def task(self, sprint_id: str, task_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM tasks WHERE sprint_id=? AND task_id=?",
            (sprint_id, task_id)).fetchone()

    def tasks(self, sprint_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM tasks WHERE sprint_id=? ORDER BY task_id",
            (sprint_id,)).fetchall())

    def transicionar(self, sprint_id: str, task_id: str, novo: str,
                     motivo: str = "") -> None:
        """Aplica uma transição validada da máquina de estados (spec §4)."""
        if novo not in ESTADOS:
            raise TransicaoInvalida(f"estado desconhecido: {novo}")
        t = self.task(sprint_id, task_id)
        if t is None:
            raise TransicaoInvalida(f"task inexistente: {sprint_id}/{task_id}")
        atual = t["estado"]
        if novo == atual:
            return
        if novo not in TRANSICOES.get(atual, set()):
            raise TransicaoInvalida(f"{task_id}: {atual} -> {novo} não permitido")
        self.conn.execute(
            "UPDATE tasks SET estado=?, atualizado_em=? WHERE sprint_id=? AND task_id=?",
            (novo, time.time(), sprint_id, task_id))
        self.evento(sprint_id, task_id, "transicao",
                    {"de": atual, "para": novo, "motivo": motivo})

    def forcar_estado(self, sprint_id: str, task_id: str, novo: str,
                      motivo: str = "forcado") -> None:
        """Só para recuperação de crash — ignora a máquina de transições.

        Limpa o motivo do bloqueio ao sair de BLOCKED: task QUEUED carregando
        "depende de [...]" é dado inconsistente e envenena quem lê o motivo depois
        (foi assim que o rearme automático parecia não funcionar).
        """
        if novo == "BLOCKED":
            self.conn.execute(
                "UPDATE tasks SET estado=?, atualizado_em=? WHERE sprint_id=? AND task_id=?",
                (novo, time.time(), sprint_id, task_id))
        else:
            self.conn.execute(
                "UPDATE tasks SET estado=?, bloqueio=NULL, atualizado_em=?"
                " WHERE sprint_id=? AND task_id=?",
                (novo, time.time(), sprint_id, task_id))
        self.evento(sprint_id, task_id, "estado_forcado", {"para": novo, "motivo": motivo})

    def bloqueia(self, sprint_id: str, task_id: str, motivo: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET estado='BLOCKED', bloqueio=?, atualizado_em=?"
            " WHERE sprint_id=? AND task_id=?",
            (motivo, time.time(), sprint_id, task_id))
        self.evento(sprint_id, task_id, "bloqueado", {"motivo": motivo})

    # ------------------------------------------------------- reabertura/desbloqueio
    def reabrir_sprint(self, sprint_id: str, *, motivo: str = "") -> str:
        """Devolve o sprint a EM_EXECUCAO depois de uma rodada que terminou.

        O laço do orquestrador grava 'FIM' no `sprint_state` quando sai (é o fim
        de UMA rodada), e `FIM` não é estado válido do ciclo de vida do sprint.
        Sem esta reabertura, retomar um sprint parado é impossível: qualquer
        transição a partir de 'FIM' é recusada pela máquina de estados.
        """
        atual = self.estado_sprint(sprint_id)
        anterior = self.ler_checkpoint(sprint_id) or {}
        onda = anterior.get("ultima_onda")
        self.checkpoint(sprint_id, "EM_EXECUCAO",
                        {**(anterior.get("checkpoint") or {}),
                         "motivo": motivo or "reaberto para nova rodada"}, onda)
        self.evento(sprint_id, None, "sprint_reaberto",
                    {"de": atual, "para": "EM_EXECUCAO", "motivo": motivo})
        return atual or ""

    def reabrir_tasks(self, sprint_id: str, task_ids: list[str], *,
                      tentativas: int = 0, motivo: str = "") -> list[str]:
        """Devolve tasks BLOCKED/FAILED para QUEUED com o contador no degrau pedido.

        O contador de tentativas é o que escolhe o modelo (escalonamento por
        tentativa): reabrir com tentativas=2 faz a PRÓXIMA tentativa ser a 3ª da
        escada, e não a 1ª. Reabrir com 0 recomeça do degrau mais barato.
        """
        reabertas: list[str] = []
        for tid in task_ids:
            row = self.conn.execute(
                "SELECT estado, tentativas FROM tasks WHERE sprint_id=? AND task_id=?",
                (sprint_id, tid)).fetchone()
            if not row:
                continue
            self.conn.execute(
                "UPDATE tasks SET estado='QUEUED', tentativas=?, tier_atual=?,"
                " bloqueio=NULL, atualizado_em=?"
                " WHERE sprint_id=? AND task_id=?",
                (tentativas, tentativas, time.time(), sprint_id, tid))
            self.evento(sprint_id, tid, "reaberto",
                        {"de": row["estado"], "para": "QUEUED",
                         "tentativas_antes": row["tentativas"],
                         "tentativas": tentativas, "motivo": motivo})
            reabertas.append(tid)
        self.conn.commit()
        return reabertas

    # ---------------------------------------------------------------- tentativas
    def proxima_tentativa(self, sprint_id: str, task_id: str) -> int:
        r = self.conn.execute(
            "SELECT COALESCE(MAX(attempt),0) m FROM attempts WHERE sprint_id=? AND task_id=?",
            (sprint_id, task_id)).fetchone()
        return int(r["m"]) + 1

    def iniciar_tentativa(self, sprint_id: str, task_id: str, *, agent: str,
                          model: str, effort: str, branch: str, worktree: str,
                          sandbox: str, base_commit: str,
                          log_path: str = "") -> int:
        n = self.proxima_tentativa(sprint_id, task_id)
        agora = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO attempts (sprint_id, task_id, attempt, agent, model,"
            " effort, branch, worktree, sandbox, base_commit, start_time, last_heartbeat,"
            " status, log_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'RUNNING',?)",
            (sprint_id, task_id, n, agent, model, effort, branch, worktree,
             sandbox, base_commit, agora, agora, log_path))
        self.evento(sprint_id, task_id, "tentativa_iniciada",
                    {"attempt": n, "agent": agent, "model": model, "effort": effort})
        return n

    def heartbeat(self, sprint_id: str, task_id: str, attempt: int) -> None:
        self.conn.execute(
            "UPDATE attempts SET last_heartbeat=? WHERE sprint_id=? AND task_id=? AND attempt=?",
            (time.time(), sprint_id, task_id, attempt))

    def finalizar_tentativa(self, sprint_id: str, task_id: str, attempt: int, *,
                            status: str, exit_code: int | None = None,
                            changed_files: Iterable[str] | None = None,
                            test_result: Any = None, review_result: Any = None,
                            final_commit: str | None = None,
                            failure_class: str | None = None,
                            fingerprint: str | None = None,
                            retry_after: float | None = None) -> None:
        self.conn.execute(
            "UPDATE attempts SET status=?, exit_code=?, end_time=?, changed_files=?,"
            " test_result=?, review_result=?, final_commit=?, failure_class=?,"
            " fingerprint=?, retry_after=? WHERE sprint_id=? AND task_id=? AND attempt=?",
            (status, exit_code, time.time(),
             json.dumps(list(changed_files or [])),
             json.dumps(test_result) if test_result is not None else None,
             json.dumps(review_result) if review_result is not None else None,
             final_commit, failure_class, fingerprint, retry_after,
             sprint_id, task_id, attempt))
        self.evento(sprint_id, task_id, "tentativa_finalizada",
                    {"attempt": attempt, "status": status, "failure_class": failure_class})

    def tentativas(self, sprint_id: str, task_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM attempts WHERE sprint_id=? AND task_id=? ORDER BY attempt",
            (sprint_id, task_id)).fetchall())

    def ultima_tentativa(self, sprint_id: str, task_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM attempts WHERE sprint_id=? AND task_id=? ORDER BY attempt DESC LIMIT 1",
            (sprint_id, task_id)).fetchone()

    # ---------------------------------------------------------------- eventos
    def evento(self, sprint_id: str | None, task_id: str | None, tipo: str,
               payload: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO events (ts, sprint_id, task_id, tipo, payload) VALUES (?,?,?,?,?)",
            (time.time(), sprint_id, task_id, tipo,
             json.dumps(payload or {}, ensure_ascii=False, default=str)))

    def eventos(self, sprint_id: str, limite: int = 500) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM events WHERE sprint_id=? ORDER BY ts DESC LIMIT ?",
            (sprint_id, limite)).fetchall())

    # ---------------------------------------------------------------- HAQ
    def haq_adicionar(self, haq_id: str, *, sprint_id: str, task_id: str | None,
                      reason: str, risk: str, dependencia: str,
                      acao_humana: str, resultado: str, verificacao: str,
                      failure_class: str = "") -> bool:
        """Idempotente por haq_id — não duplica entradas (spec §22)."""
        antes = self.conn.total_changes
        self.conn.execute(
            "INSERT OR IGNORE INTO haq (haq_id, sprint_id, task_id, reason, risk,"
            " dependencia, acao_humana, resultado, verificacao, criado_em, status,"
            " failure_class) VALUES (?,?,?,?,?,?,?,?,?,?, 'OPEN', ?)",
            (haq_id, sprint_id, task_id, reason, risk, dependencia,
             acao_humana, resultado, verificacao, time.time(), failure_class))
        novo = self.conn.total_changes > antes
        if novo:
            self.evento(sprint_id, task_id, "haq_criado", {"haq_id": haq_id})
        return novo

    def haq_resolver(self, sprint_id: str, haq_id: str, *, resultado: str = "",
                     verificacao: str = "") -> bool:
        """Fecha uma HAQ com a decisão registrada. False se ela não existia.

        A fila humana precisa de um caminho de SAÍDA: sem isto, uma HAQ decidida
        continua contando como atenção humana no HAR para sempre.
        """
        r = self.conn.execute("SELECT haq_id FROM haq WHERE haq_id=?",
                              (haq_id,)).fetchone()
        if not r:
            return False
        self.conn.execute(
            "UPDATE haq SET status='DONE', resultado=COALESCE(NULLIF(?, ''),"
            " resultado), verificacao=COALESCE(NULLIF(?, ''), verificacao)"
            " WHERE haq_id=?", (resultado, verificacao, haq_id))
        self.evento(sprint_id, None, "haq_resolvido",
                    {"haq_id": haq_id, "resultado": resultado})
        self.conn.commit()
        return True

    def haq_listar(self, sprint_id: str) -> list[sqlite3.Row]:
        return list(self.conn.execute(
            "SELECT * FROM haq WHERE sprint_id=? ORDER BY haq_id", (sprint_id,)).fetchall())

    # ---------------------------------------------------------------- kill switch
    def killswitch_set(self, nivel: str, ativo: bool, *, alvo: str = "",
                       motivo: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO killswitch (nivel, alvo, ativo, motivo, ts)"
            " VALUES (?,?,?,?,?)", (nivel, alvo, int(ativo), motivo, time.time()))
        self.evento(None, None, "killswitch", {"nivel": nivel, "ativo": ativo,
                                               "alvo": alvo, "motivo": motivo})

    def killswitch_ativo(self, sprint_id: str | None = None,
                         task_id: str | None = None,
                         projeto: str = "Coding_Machine") -> str | None:
        """Devolve o nível de parada aplicável, ou None. Hierárquico (spec §26)."""
        for nivel, alvo in (("STOP_ALL", None),
                            ("STOP_PROJECT", projeto),
                            ("STOP_SPRINT", sprint_id),
                            ("STOP_TASK", task_id)):
            if nivel == "STOP_ALL":
                r = self.conn.execute(
                    "SELECT ativo FROM killswitch WHERE nivel='STOP_ALL'").fetchone()
                if r and r["ativo"]:
                    return "STOP_ALL"
                continue
            if alvo is None:
                continue
            r = self.conn.execute(
                "SELECT ativo FROM killswitch WHERE nivel=? AND alvo=?",
                (nivel, alvo)).fetchone()
            if r and r["ativo"]:
                return nivel
        return None

    def killswitch_limpar(self) -> None:
        self.conn.execute("UPDATE killswitch SET ativo=0")

    # ---------------------------------------------------------------- escritor único
    def adquirir_worktree(self, worktree: str, task_id: str, agent: str) -> None:
        """Um único escritor ativo por worktree (spec §15/§21)."""
        with self.tx():
            r = self.conn.execute("SELECT * FROM writer_lock WHERE worktree=?",
                                  (worktree,)).fetchone()
            if r and r["task_id"] != task_id:
                raise WorktreeOcupado(
                    f"{worktree} já pertence a {r['task_id']} ({r['agent']})")
            self.conn.execute(
                "INSERT OR REPLACE INTO writer_lock (worktree, task_id, agent, pid, heartbeat)"
                " VALUES (?,?,?,?,?)", (worktree, task_id, agent, 0, time.time()))

    def liberar_worktree(self, worktree: str) -> None:
        self.conn.execute("DELETE FROM writer_lock WHERE worktree=?", (worktree,))

    # ------------------------------------------------- rodada única por sprint
    @staticmethod
    def _pid_vivo(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except (OSError, ValueError):
            return False

    def adquirir_run_lock(self, sprint_id: str, pid: int | None = None,
                          validade_s: float = 3600) -> tuple[bool, str]:
        """Toma o direito de rodar este sprint. (True, motivo) = é esta rodada.

        Recusa se houver outra rodada VIVA. "Viva" = processo com o pid registrado
        andando; se o pid morreu (crash/kill) e o heartbeat passou da validade, o
        lock é assumido como órfão e a rodada nova prossegue — nunca fica travado
        para sempre por causa de um processo morto.
        """
        pid = os.getpid() if pid is None else pid
        r = self.conn.execute("SELECT * FROM run_lock WHERE sprint_id=?",
                              (sprint_id,)).fetchone()
        agora = time.time()
        if r:
            vivo = self._pid_vivo(r["pid"])
            idade = agora - float(r["heartbeat"] or 0)
            if vivo and int(r["pid"]) != pid:
                return False, (f"já existe rodada viva no sprint {sprint_id} "
                               f"(pid {r['pid']}, heartbeat há {idade / 60:.0f} min)")
            if not vivo and idade < validade_s and int(r["pid"]) != pid:
                # pid morto mas heartbeat fresco: pode ser outra máquina/sessão
                # renovando; na dúvida, não rouba.
                return False, (f"rodada registrada sem processo local vivo "
                               f"(pid {r['pid']}, heartbeat há {idade / 60:.0f} min)")
        with self.tx():
            if r:
                self.conn.execute(
                    "UPDATE run_lock SET pid=?, heartbeat=?, nota=? WHERE sprint_id=?",
                    (pid, agora,
                     f"assumiu de pid {r['pid']} (sem processo vivo)" if r["pid"] != pid
                     else (r["nota"] or ""), sprint_id))
            else:
                self.conn.execute(
                    "INSERT INTO run_lock (sprint_id, pid, iniciado_em, heartbeat, nota)"
                    " VALUES (?,?,?,?,?)", (sprint_id, pid, agora, agora, "rodada dona"))
        return True, "rodada única"

    def renovar_lock(self, sprint_id: str, pid: int | None = None) -> None:
        """Batimento dos locks desta rodada: run_lock + writer_lock do sprint.

        O writer_lock só era escrito no início da tentativa e não era reescrito
        durante a revisão (que leva minutos) — quem observa de fora (tela, guarda,
        vigia) via um heartbeat parado e concluía que a rodada tinha morrido.
        """
        pid = os.getpid() if pid is None else pid
        agora = time.time()
        self.conn.execute("UPDATE run_lock SET heartbeat=? WHERE sprint_id=? AND pid=?",
                          (agora, sprint_id, pid))
        self.conn.execute(
            "UPDATE writer_lock SET heartbeat=? WHERE (pid=? OR pid IS NULL OR pid=0)"
            " AND task_id IN (SELECT task_id FROM tasks WHERE sprint_id=?)",
            (agora, pid, sprint_id))
        self.conn.commit()

    def liberar_run_lock(self, sprint_id: str, pid: int | None = None) -> None:
        """Libera só se o lock for DESTA rodada (não derruba o dono novo)."""
        pid = os.getpid() if pid is None else pid
        self.conn.execute("DELETE FROM run_lock WHERE sprint_id=? AND pid=?",
                          (sprint_id, pid))
        self.conn.commit()

    def run_lock(self, sprint_id: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM run_lock WHERE sprint_id=?",
                              (sprint_id,)).fetchone()
        return dict(r) if r else None

    def stale_workers(self, timeout_s: float = 900) -> list[sqlite3.Row]:
        """Tentativas RUNNING cujo heartbeat parou (spec §21)."""
        limite = time.time() - timeout_s
        return list(self.conn.execute(
            "SELECT * FROM attempts WHERE status='RUNNING' AND"
            " COALESCE(last_heartbeat, start_time) < ?", (limite,)).fetchall())

    # ---------------------------------------------------------------- esperas de cota
    def registrar_espera(self, sprint_id: str, task_id: str, agent: str,
                         retry_after: float, motivo: str) -> None:
        self.conn.execute(
            "INSERT INTO resource_waits (sprint_id, task_id, agent, detectado_em,"
            " retry_after, motivo) VALUES (?,?,?,?,?,?)",
            (sprint_id, task_id, agent, time.time(), retry_after, motivo))
        self.evento(sprint_id, task_id, "espera_recurso",
                    {"retry_after": retry_after, "motivo": motivo})

    def esperas_vencidas(self, agora: float | None = None) -> list[sqlite3.Row]:
        agora = agora or time.time()
        return list(self.conn.execute(
            "SELECT * FROM resource_waits WHERE resolvido=0 AND retry_after<=?",
            (agora,)).fetchall())

    def espera_pendente(self, sprint_id: str, task_id: str,
                        agora: float | None = None) -> float | None:
        """`retry_after` da espera de recurso ainda NÃO vencida, ou None.

        É o portão que faz "cota é espera de recurso, não falha" valer de fato:
        sem ele o orquestrador reinvocava o agente antes da hora e as 5h10m de
        espera nunca aconteciam — só eram registradas.
        """
        agora = agora or time.time()
        r = self.conn.execute(
            "SELECT MIN(retry_after) AS t FROM resource_waits"
            " WHERE sprint_id=? AND task_id=? AND resolvido=0 AND retry_after>?",
            (sprint_id, task_id, agora)).fetchone()
        return r["t"] if r and r["t"] is not None else None

    def resolver_esperas(self, sprint_id: str, task_id: str) -> int:
        """Marca as esperas da task como resolvidas (vamos tentar de novo)."""
        antes = self.conn.total_changes
        self.conn.execute(
            "UPDATE resource_waits SET resolvido=1 WHERE sprint_id=? AND task_id=?"
            " AND resolvido=0", (sprint_id, task_id))
        return self.conn.total_changes - antes

    # ---------------------------------------------------------------- checkpoint
    def checkpoint(self, sprint_id: str, estado: str, dados: dict,
                   onda: int | None = None) -> None:
        agora = time.time()
        payload = json.dumps(dados, ensure_ascii=False, default=str)
        # estado ATUAL (upsert) ...
        self.conn.execute(
            "INSERT OR REPLACE INTO sprint_state (sprint_id, estado, checkpoint,"
            " ultima_onda, atualizado_em) VALUES (?,?,?,?,?)",
            (sprint_id, estado, payload, onda, agora))
        # ... e o HISTÓRICO (append), para auditar retomadas.
        self.conn.execute(
            "INSERT INTO checkpoints (sprint_id, estado, checkpoint, onda, criado_em)"
            " VALUES (?,?,?,?,?)", (sprint_id, estado, payload, onda, agora))
        self.conn.commit()

    def ler_checkpoint(self, sprint_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM sprint_state WHERE sprint_id=?", (sprint_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["checkpoint"] = json.loads(d["checkpoint"] or "{}")
        return d

    def historico_checkpoints(self, sprint_id: str, limite: int = 100) -> list[dict]:
        """Todos os checkpoints do sprint, do mais recente para o mais antigo."""
        rows = self.conn.execute(
            "SELECT * FROM checkpoints WHERE sprint_id=? ORDER BY criado_em DESC"
            " LIMIT ?", (sprint_id, limite)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["checkpoint"] = json.loads(d["checkpoint"] or "{}")
            out.append(d)
        return out

    def ultimo_checkpoint_em(self, sprint_id: str, estado: str) -> dict | None:
        """Checkpoint mais recente com um dado estado (ex.: WAITING_RESOURCE)."""
        for c in self.historico_checkpoints(sprint_id):
            if c["estado"] == estado:
                return c
        return None

    # ------------------------------------------------------- ciclo de vida do sprint
    def estado_sprint(self, sprint_id: str) -> str | None:
        """Estado atual do sprint, ou None se nunca foi registrado.

        Fonte autoritativa é o banco. O `status:` do sprint.yaml é declaração de
        intenção, escrita à mão — não o fato.
        """
        r = self.conn.execute(
            "SELECT estado FROM sprint_state WHERE sprint_id=?",
            (sprint_id,)).fetchone()
        return r["estado"] if r else None

    def transicionar_sprint(self, sprint_id: str, novo: str, motivo: str = "",
                            dados: dict | None = None) -> None:
        atual = self.estado_sprint(sprint_id) or "PLANEJADO"
        if novo not in TRANSICOES_SPRINT.get(atual, set()):
            raise TransicaoInvalida(
                f"sprint {sprint_id}: {atual} -> {novo} não permitido")
        anterior = self.ler_checkpoint(sprint_id) or {}
        self.checkpoint(sprint_id, novo, {**(anterior.get("checkpoint") or {}),
                                          **(dados or {}), "motivo": motivo})
        self.evento(sprint_id, None, "sprint_transicao",
                    {"de": atual, "para": novo, "motivo": motivo})

    def pendentes(self, sprint_id: str) -> list[str]:
        """Tasks fora de estado terminal, como 'T03(REVIEW)'."""
        rows = self.conn.execute(
            "SELECT task_id, estado FROM tasks WHERE sprint_id=?"
            " AND estado NOT IN ('DONE','INTEGRATED') ORDER BY task_id",
            (sprint_id,)).fetchall()
        return [f"{r['task_id']}({r['estado']})" for r in rows]

    def encerrar_sprint(self, sprint_id: str, *, resultado: str = "CONCLUIDO",
                        resumo: str = "", forcar: bool = False) -> None:
        """Fecha o sprint em ENCERRADO.

        Recusa se houver task fora de estado terminal — encerrar com trabalho em
        aberto é exatamente o que o HAQ existe para evitar.

        Avança pelas transições VÁLIDAS em vez de gravar ENCERRADO direto: o
        histórico do sprint precisa mostrar por onde ele passou, e a máquina de
        estados vale também para quem está fechando.
        """
        atual = self.estado_sprint(sprint_id)
        if atual in SPRINT_TERMINAIS and not forcar:
            raise SprintNaoEncerravel(f"sprint {sprint_id} já está {atual}")
        pend = self.pendentes(sprint_id)
        if pend and not forcar:
            raise SprintNaoEncerravel(
                f"{len(pend)} task(s) fora de estado terminal: {', '.join(pend)}")

        partida = atual or "PLANEJADO"
        metadados = {"resultado": resultado, "resumo": resumo,
                     "estado_anterior": atual,
                     "tasks_pendentes_no_encerramento": pend,
                     "forcado": bool(forcar)}
        if partida in ORDEM_SPRINT:
            caminho = ORDEM_SPRINT[ORDEM_SPRINT.index(partida) + 1:]
        elif forcar:
            caminho = []
        else:
            raise SprintNaoEncerravel(
                f"sprint {sprint_id} está {partida}; use --forcar para encerrar")

        # O checkpoint de metadados vai JUNTO com o passo final, senao ENCERRADO
        # fica gravado duas vezes (uma pela transicao, outra pelo checkpoint).
        for passo in caminho:
            self.transicionar_sprint(
                sprint_id, passo, f"encerramento: {resultado}",
                metadados if passo == "ENCERRADO" else None)
        if not caminho:      # sprint travado encerrado com --forcar
            self.checkpoint(sprint_id, "ENCERRADO", metadados)

        self.evento(sprint_id, None, "sprint_encerrado",
                    {"resultado": resultado, "resumo": resumo,
                     "pendentes": pend, "forcado": bool(forcar)})

    # --------------------------------------------- conclusão por evidência (rota b)
    def concluir_task_evidenciada(self, sprint_id: str, task_id: str, *,
                                  evidencia: str, test_result: Any = None,
                                  commit: str = "", agente: str = "retroativo",
                                  origem: str = "retroativo",
                                  motivo: str = "evidência retroativa") -> int:
        """Conclui uma task a partir de evidência JÁ existente.

        Para quando o trabalho foi feito fora do laço do orquestrador: o código
        existe e os testes passam, mas a task nunca passou por RUNNING/VERIFYING.

        Percorre o caminho VÁLIDO de transições em vez de forçar o estado, então
        a máquina de estados continua valendo. A tentativa fica marcada com
        `origem='retroativo'` — ninguém deve confundir isto com uma execução real
        do orquestrador, e o relatório mostra a diferença.
        """
        linha = self.task(sprint_id, task_id)
        if linha is None:
            raise KeyError(f"task inexistente: {sprint_id}/{task_id}")
        # Caminho válido até DONE. Andar a partir de onde a task ESTÁ evita o
        # erro de tentar DONE->PLANNED quando ela já foi concluída antes.
        ordem = ["NEW", "PLANNED", "QUEUED", "RUNNING", "VERIFYING", "DONE"]
        estado = linha["estado"]
        if estado in TERMINAIS_TASK or estado == "DONE":
            raise ValueError(f"task {task_id} já está {estado}")
        if estado not in ordem:
            raise ValueError(
                f"task {task_id} está em {estado}; conclusão por evidência parte"
                f" de um dos estados {ordem}")
        for passo in ordem[ordem.index(estado) + 1:]:
            self.transicionar(sprint_id, task_id, passo, motivo)
        att = self.iniciar_tentativa(
            sprint_id, task_id, agent=agente, model="(nenhum)",
            effort="(nenhum)", branch="main", worktree="",
            sandbox="(nenhum)", base_commit=commit)
        self.finalizar_tentativa(
            sprint_id, task_id, att, status="OK", exit_code=0,
            test_result=test_result, final_commit=commit)
        self.conn.execute(
            "UPDATE attempts SET origem=? WHERE sprint_id=? AND task_id=?"
            " AND attempt=?", (origem, sprint_id, task_id, att))
        self.evento(sprint_id, task_id, "task_concluida_por_evidencia",
                    {"attempt": att, "evidencia": evidencia, "commit": commit,
                     "origem": origem})
        return att

    # ---------------------------------------------------------------- métricas
    def metricas(self, sprint_id: str) -> dict:
        q = lambda sql, *a: self.conn.execute(sql, a).fetchone()[0]  # noqa: E731
        por_estado = {r["estado"]: r["n"] for r in self.conn.execute(
            "SELECT estado, COUNT(*) n FROM tasks WHERE sprint_id=? GROUP BY estado",
            (sprint_id,))}
        return {
            "tasks_total": q("SELECT COUNT(*) FROM tasks WHERE sprint_id=?", sprint_id),
            "por_estado": por_estado,
            "done": por_estado.get("DONE", 0) + por_estado.get("INTEGRATED", 0),
            "blocked": por_estado.get("BLOCKED", 0),
            "failed": por_estado.get("FAILED", 0),
            "waiting_resource": por_estado.get("WAITING_RESOURCE", 0),
            "tentativas_implementacao": q(
                "SELECT COUNT(*) FROM attempts WHERE sprint_id=?", sprint_id),
            "tentativas_total": q(
                "SELECT COUNT(*) FROM attempts WHERE sprint_id=?", sprint_id),
            # Tentativas que o orquestrador REALMENTE executou. Conclusão por
            # evidência (origem='retroativo') cria linha em attempts para ficar
            # auditável, mas não foi execução do laço — somar as duas coisas
            # inflaria o custo do sprint.
            "tentativas_orquestrador": q(
                "SELECT COUNT(*) FROM attempts WHERE sprint_id=?"
                " AND COALESCE(origem,'orquestrador') NOT IN ('retroativo')",
                sprint_id),
            "escalonamentos": q(
                "SELECT COUNT(*) FROM attempts WHERE sprint_id=? AND CAST(json_extract("
                "test_result,'$.tier') AS INTEGER) > 0", sprint_id),
            "esperas_cota": q(
                "SELECT COUNT(*) FROM resource_waits WHERE sprint_id=?", sprint_id),
            "haq": q("SELECT COUNT(*) FROM haq WHERE sprint_id=?", sprint_id),
            "haq_abertos": q("SELECT COUNT(*) FROM haq WHERE sprint_id=? AND status='OPEN'",
                             sprint_id),
            "eventos": q("SELECT COUNT(*) FROM events WHERE sprint_id=?", sprint_id),
        }
