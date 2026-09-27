"""DEVFACTORY — store de estado persistente em SQLite (T03, spec §4/§7/§22).

Esta é a fonte autoritativa do Sprint, junto com Git e os arquivos do sprint.
Memória conversacional NUNCA é usada como estado (spec §7).

Todas as operações são idempotentes: reexecutar após crash não duplica nada
(spec §22). O schema usa CREATE TABLE IF NOT EXISTS e INSERT OR IGNORE onde faz
sentido.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .config import ESTADOS, TRANSICOES

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


class TransicaoInvalida(Exception):
    pass


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
        """Só para recuperação de crash — ignora a máquina de transições."""
        self.conn.execute(
            "UPDATE tasks SET estado=?, atualizado_em=? WHERE sprint_id=? AND task_id=?",
            (novo, time.time(), sprint_id, task_id))
        self.evento(sprint_id, task_id, "estado_forcado", {"para": novo, "motivo": motivo})

    def bloqueia(self, sprint_id: str, task_id: str, motivo: str) -> None:
        self.conn.execute(
            "UPDATE tasks SET estado='BLOCKED', bloqueio=?, atualizado_em=?"
            " WHERE sprint_id=? AND task_id=?",
            (motivo, time.time(), sprint_id, task_id))
        self.evento(sprint_id, task_id, "bloqueado", {"motivo": motivo})

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
