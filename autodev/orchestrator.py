"""DEVFACTORY — orquestrador de longo horizonte (spec §5/§7/§21/§30).

Padrão de operação:
    RESTORE STATE -> SELECT NEXT BOUNDED TASK -> EXECUTE -> VERIFY -> CHECKPOINT
    -> SELECT NEXT TASK

O estado autoritativo é Git + SQLite + arquivos do sprint + evidência de teste.
Nunca a memória conversacional.
"""
from __future__ import annotations

import json
import os
import signal
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import agents, haq, integration, killswitch, report, retry, review
from . import sandbox as sbx
from . import testrunner
from .config import Config, carrega_dag, carrega_sprint, ordem_topologica
from .state import StateStore, TransicaoInvalida, WorktreeOcupado
from .worktree import (WorktreeManager, arquivos_alterados, branch_existe,
                       commit_atual, git)

PROMPT_TASK = """Voce e o agente de implementacao de uma task de um Sprint autonomo.

## Task {task_id}: {titulo}

## Criterios de aceitacao (todos obrigatorios)
{criterios}

## Contexto do projeto
{contexto}

## Restricoes
- Trabalhe SOMENTE dentro deste worktree: {worktree}
- Nao use sudo, nao leia credenciais, nao escreva fora deste diretorio.
- Nao adicione dependencia nova sem necessidade demonstravel.
- Se houver testes, eles devem passar ao final.
- Simplicidade primeiro. Sem infraestrutura que a task nao exige.

## O que entregar
1. Implemente a task de forma minima e completa.
2. Escreva ou ajuste testes que provem o comportamento (se aplicavel).
3. Rode os testes e corrija o que falhar.
4. Ao terminar, responda com uma linha final:
   RESULTADO: <o que foi implementado> | TESTES: <comando e resultado>
"""


@dataclass
class ResumoTask:
    task_id: str
    estado_final: str
    tentativas: int
    esperas_cota: int = 0
    review: dict | None = None
    teste: dict | None = None
    motivo: str = ""


@dataclass
class ResultadoSprint:
    sprint: str
    tasks: list[ResumoTask] = field(default_factory=list)
    parado_por: str | None = None
    duracao_s: float = 0.0
    # True quando o único motivo de não haver progresso é espera de cota: é
    # "volte depois", não travamento — o vigia usa isso para decidir se retoma.
    aguardando_recurso: bool = False

    @property
    def concluidas(self) -> int:
        return sum(1 for t in self.tasks if t.estado_final in ("DONE", "INTEGRATED"))


class Orquestrador:
    def __init__(self, raiz: str | Path, sprint: str, *,
                 modo_teste: bool = False, deadline_s: float | None = None):
        self.raiz = Path(raiz).resolve()
        self.sprint = sprint
        self.modo_teste = modo_teste
        self.deadline = time.time() + deadline_s if deadline_s else None
        self.cfg = Config.carregar()
        self.dir_sprint = self.raiz / ".autodev" / "sprints" / sprint
        self.dir_sprint.mkdir(parents=True, exist_ok=True)
        for sub in ("evidence", "logs"):
            (self.dir_sprint / sub).mkdir(exist_ok=True)
        self.store = StateStore(self.raiz / ".autodev" / "state.db")
        self.wm = WorktreeManager(self.raiz, self.raiz / ".autodev" / "worktrees")
        self.disponiveis = agents.detectar(self.cfg)
        self.dag: dict = {}
        self._parar = False
        signal.signal(signal.SIGINT, self._sinal)
        signal.signal(signal.SIGTERM, self._sinal)

    def _sinal(self, *_):
        self._parar = True
        self.log("sinal de parada recebido — finalizando com checkpoint")

    # ------------------------------------------------------------ utilidades
    def log(self, msg: str) -> None:
        linha = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(linha, flush=True)
        with (self.dir_sprint / "logs" / "orquestrador.log").open("a",
                                                                 encoding="utf-8") as fh:
            fh.write(linha + "\n")

    def _checa_parada(self, acao: str, task: str | None = None) -> None:
        killswitch.verificar(self.store, self.raiz, sprint=self.sprint,
                             task=task, acao=acao)
        if self._parar:
            raise killswitch.ParadoPorKillSwitch("SIGTERM", "parada solicitada por sinal")
        if self.deadline and time.time() > self.deadline:
            raise killswitch.ParadoPorKillSwitch("DEADLINE", "deadline do sprint atingido")

    def _ctx(self) -> str:
        p = self.dir_sprint / "spec.md"
        return p.read_text(encoding="utf-8")[:2500] if p.exists() else "(sem spec.md)"

    def _row(self, task_id: str):
        """Row da task, garantidamente existente (falha alto se não existir)."""
        r = self.store.task(self.sprint, task_id)
        if r is None:
            raise KeyError(f"task {task_id} nao existe no sprint {self.sprint}")
        return r

    def _prompt_continuacao(self, task_id: str, spec: dict, criterios: list[str],
                            wt, base: str, prompt_base: str) -> str:
        """Monta o prompt de CONTINUAÇÃO para retomada após espera de cota (§12).

        Inspeciona o worktree de verdade: quais testes passam, quais falham, o que
        já está commitado. O agente recebe o estado atual e a ordem explícita de
        continuar — nunca o prompt original, que o faria recomeçar do zero.
        """
        anterior = self.store.ultima_tentativa(self.sprint, task_id)
        try:
            rt = testrunner.rodar(wt.caminho, comando=spec.get("teste"))
            passando, falhando = testrunner.resumo_testes(rt.saida)
        except Exception as e:  # noqa: BLE001
            passando, falhando = [], [f"(nao foi possivel rodar os testes: {e})"]
        alterados = []
        try:
            alterados = arquivos_alterados(wt.caminho, base)
        except Exception:  # noqa: BLE001
            pass
        ck = self.store.ler_checkpoint(self.sprint) or {}
        det = ck.get("checkpoint") or {}
        findings = []
        if anterior and anterior["review_result"]:
            try:
                import json as _json
                findings = (_json.loads(anterior["review_result"]) or {}).get(
                    "findings", []) or []
            except Exception:  # noqa: BLE001
                findings = []
        tentativas = [{"attempt": t["attempt"], "failure_class": t["failure_class"],
                       "agent": t["agent"], "model": t["model"]}
                      for t in self.store.tentativas(self.sprint, task_id)]
        proxima = ("Retomar de onde parou: os arquivos ja modificados estao no "
                   "worktree. Corrija o que falta para os testes passarem e "
                   "NAO reescreva o que ja funciona.")
        prompt = retry.montar_prompt_continuacao(
            prompt_base, task_id=task_id, criterios=criterios,
            estado_impl=f"{len(alterados)} arquivo(s) ja modificado(s) por tentativa "
                        f"anterior; ultimo commit {det.get('commit', '(nao registrado)')}",
            arquivos=alterados,
            ultimo_commit=det.get("commit") or commit_atual(wt.caminho),
            testes_passando=passando, testes_falhando=falhando,
            tentativas=tentativas, findings=findings, proxima_acao=proxima)
        return prompt

    def _onda(self, task_id: str) -> int:
        for i, onda in enumerate(ordem_topologica(self.dag)):
            if task_id in onda:
                return i
        return 0

    # ------------------------------------------------------------ carregar sprint
    def carregar(self) -> None:
        self.sprint_yaml = carrega_sprint(self.dir_sprint / "sprint.yaml")
        self.dag = carrega_dag(self.dir_sprint / "dag.json")
        novas = self.store.criar_tasks_do_dag(self.sprint, self.dag)
        self.log(f"sprint {self.sprint}: {len(self.dag['tasks'])} tasks "
                 f"({novas} novas) — ondas {ordem_topologica(self.dag)}")

    # ------------------------------------------------------------ recuperação
    def recuperar(self) -> dict:
        """Recuperação de crash (spec §21): retoma o que ficou pela metade."""
        rel: dict = {"stale": [], "requeued": [], "resource": []}
        stale = self.store.stale_workers(
            self.cfg.policies["heartbeat"]["stale_apos_segundos"])
        for t in stale:
            rel["stale"].append(f"{t['task_id']}#{t['attempt']}")
            self.store.finalizar_tentativa(
                t["sprint_id"], t["task_id"], t["attempt"], status="STALE",
                failure_class="AGENT_CRASH",
                test_result={"nota": "worker sem heartbeat — recuperado no restart"})
            row = self.store.task(t["sprint_id"], t["task_id"])
            if row and row["estado"] == "RUNNING":
                self.store.forcar_estado(t["sprint_id"], t["task_id"], "RETRY",
                                         "recuperado de worker stale")
                rel["requeued"].append(t["task_id"])
            if t["worktree"]:
                self.store.liberar_worktree(t["worktree"])
        # esperas de cota vencidas
        for w in self.store.esperas_vencidas():
            rel["resource"].append(w["task_id"])
        if any(rel.values()):
            self.log(f"recuperacao: {rel}")
        return rel

    # --------------------------------------------------------------- base
    def _base_do_worktree(self) -> str:
        """Commit base do worktree de uma task.

        O DAG era respeitado para ORDEM e nunca para CONTEUDO. `WorktreeManager`
        criava todo worktree a partir da `main`, entao uma task que declarava
        `deps` esperava pela dependencia e mesmo assim comecava SEM o codigo dela:
        reimplementava o que ja existia e escrevia a sua propria versao dos mesmos
        arquivos — e no merge as versoes colidiam. Numa noite real isso deu 1 task
        integrada de 10, com as outras 9 em CONFLITO em cinco rodadas seguidas.

        A integracao acontece a cada onda, entao o tip do branch de integracao ja
        contem o trabalho integrado das dependencias. Base e o que o DAG prometia.
        """
        branch = f"sprint/{self.sprint}/integration"
        if branch_existe(self.raiz, branch):
            return git("rev-parse", branch, cwd=self.raiz).strip()
        return commit_atual(self.raiz)

    # ------------------------------------------------------------ uma task
    def executar_task(self, task_id: str) -> ResumoTask:
        spec = next(t for t in self.dag["tasks"] if t["id"] == task_id)
        criterios = spec["criterios"]
        agente = spec.get("agente", "codex")
        tentativas = self.store.tentativas(self.sprint, task_id)
        fps = [t["fingerprint"] for t in tentativas if t["fingerprint"]]

        row = self._row(task_id)
        # Estado de entrada importa: retomar de WAITING_RESOURCE exige prompt de
        # CONTINUAÇÃO, não reinício (spec §12).
        estado_entrada = row["estado"]
        # `row` é um snapshot: precisa ser reatribuído após cada transição, senão
        # o passo NEW->PLANNED fica invisível para o `if` seguinte e a task trava
        # em PLANNED (bug real que derrubava a execução inteira).
        estado = estado_entrada
        if estado == "NEW":
            self.store.transicionar(self.sprint, task_id, "PLANNED")
            estado = "PLANNED"
        if estado in ("PLANNED", "RETRY", "WAITING_RESOURCE", "BLOCKED"):
            self._checa_parada("criar_worktree", task_id)
            self.store.forcar_estado(self.sprint, task_id, "QUEUED",
                                     "selecionado pelo orquestrador")

        wt = self.wm.criar(self.sprint, task_id, agente,
                           base=self._base_do_worktree())
        try:
            self.store.adquirir_worktree(str(wt.caminho), task_id, agente)
        except WorktreeOcupado as e:
            self.store.bloqueia(self.sprint, task_id, str(e))
            return ResumoTask(task_id, "BLOCKED", len(tentativas), motivo=str(e))

        base = wt.base_commit
        historico: list[dict] = []
        esperas = 0

        while True:
            self._checa_parada("invocar_agente", task_id)
            r = self._row(task_id)
            n_tent = r["tentativas"]

            if n_tent >= self.cfg.policies["retry"]["max_tentativas_implementacao"]:
                self.store.bloqueia(self.sprint, task_id,
                                    f"limite de {n_tent} tentativas de implementacao")
                self.store.liberar_worktree(str(wt.caminho))
                return ResumoTask(task_id, "BLOCKED", n_tent, esperas,
                                  motivo="limite de tentativas")

            # ---- decisão de retry/escalonamento --------------------------------
            if historico:
                d = retry.decidir(
                    failure_class=historico[-1]["failure_class"],
                    tentativas_implementacao=n_tent, esperas_cota=esperas,
                    agente_atual=agente, cfg=self.cfg, modo_teste=self.modo_teste,
                    fp_nova=historico[-1].get("fingerprint", ""), fps_anteriores=fps)
                if d.estrategia == retry.Estrategia.BLOQUEAR:
                    self.store.bloqueia(self.sprint, task_id, d.motivo)
                    self.store.liberar_worktree(str(wt.caminho))
                    return ResumoTask(task_id, "BLOCKED", n_tent, esperas,
                                      motivo=d.motivo)
                if d.estrategia == retry.Estrategia.TROCAR_AGENTE and d.agente:
                    agente = d.agente
                    wt = self.wm.criar(self.sprint, task_id, agente, base=base)
                    self.store.adquirir_worktree(str(wt.caminho), task_id, agente)
                modelo_info = self.cfg.modelo_para_tentativa(d.tentativa_proxima)
            else:
                d = None
                modelo_info = self.cfg.modelo_para_tentativa(1)

            # ---- monta o prompt (com evidência nova, nunca o mesmo prompt) -----
            prompt = PROMPT_TASK.format(
                task_id=task_id, titulo=spec["titulo"],
                criterios="\n".join(f"- {c}" for c in criterios),
                contexto=self._ctx(), worktree=wt.caminho)
            if d and historico:
                prompt = retry.montar_prompt_retry(
                    prompt, decisao=d, saida_testes=historico[-1].get("saida", ""),
                    arquivos=historico[-1].get("arquivos", []),
                    tentativas_anteriores=historico)

            # ---- retomada pós-cota: CONTINUAR o trabalho, não recomeçar (spec §12)
            if estado_entrada == "WAITING_RESOURCE" and not historico:
                prompt = self._prompt_continuacao(
                    task_id, spec, criterios, wt, base, prompt)

            # ---- invoca ---------------------------------------------------------
            modelo = modelo_info["slug"] if agente == "codex" else None
            effort = modelo_info["effort"] if agente == "codex" else None
            log_path = self.dir_sprint / "logs" / f"{task_id}-t{n_tent + 1}-{agente}.log"
            att = self.store.iniciar_tentativa(
                self.sprint, task_id, agent=agente, model=modelo or "", effort=effort or "",
                branch=wt.branch, worktree=str(wt.caminho),
                sandbox="bwrap" if sbx.disponivel() else "nenhum",
                base_commit=base, log_path=str(log_path))
            # ---- re-enfileira antes de rodar -----------------------------------
            # O laço de retry volta para cá com a task em RETRY (ou REVIEW,
            # VERIFYING). A máquina de estados exige RETRY -> QUEUED -> RUNNING;
            # pular o QUEUED derrubava o Sprint inteiro na 2ª tentativa.
            estado_agora = self._row(task_id)["estado"]
            if estado_agora in ("RETRY", "VERIFYING", "REVIEW", "WAITING_RESOURCE",
                                "BLOCKED"):
                self.store.forcar_estado(self.sprint, task_id, "QUEUED",
                                         f"reenfileirado (vindo de {estado_agora})")

            self.store.transicionar(self.sprint, task_id, "RUNNING",
                                    f"tentativa {att} com {agente}")
            self.store.conn.execute(
                "UPDATE tasks SET tentativas=?, tier_atual=?, agente=?, worktree=?,"
                " branch=?, atualizado_em=? WHERE sprint_id=? AND task_id=?",
                (n_tent + 1, modelo_info["tier"], agente, str(wt.caminho),
                 wt.branch, time.time(), self.sprint, task_id))

            fake_spec = os.environ.get("AUTODEV_FAKE_SPEC", "")
            self.log(f"{task_id} tentativa {att}: {agente} "
                     f"{modelo or '(default)'}/{effort or '(default)'}")
            res = agents.invocar(agents.Invocacao(
                agente=agente, prompt=prompt, worktree=str(wt.caminho),
                modelo=modelo, effort=effort, edita=True,
                timeout=int(os.environ.get("AUTODEV_AGENT_TIMEOUT", "900")),
                log_path=str(log_path), fake_script=fake_spec or None), self.cfg)

            # ---- cota do Codex: espera de RECURSO, não falha --------------------
            if res.failure_class == "CODEX_QUOTA":
                espera = self.cfg.espera_cota(self.modo_teste)
                retry_after = time.time() + espera
                esperas += 1
                self.store.finalizar_tentativa(
                    self.sprint, task_id, att, status="WAITING_RESOURCE",
                    exit_code=res.exit_code, failure_class="CODEX_QUOTA",
                    retry_after=retry_after,
                    test_result={"nota": "cota esgotada — task preservada, nao falhou"})
                self.store.registrar_espera(self.sprint, task_id, agente,
                                            retry_after, "cota do Codex esgotada")
                self.store.transicionar(self.sprint, task_id, "WAITING_RESOURCE",
                                        "cota do Codex")
                self.store.conn.execute(
                    "UPDATE tasks SET esperas_cota=esperas_cota+1 WHERE sprint_id=?"
                    " AND task_id=?", (self.sprint, task_id))
                # salva o trabalho já feito antes de esperar
                c = self.wm.commit(wt.caminho, f"{task_id}: checkpoint antes de "
                                               f"aguardar cota ({att})")
                self.store.checkpoint(self.sprint, "WAITING_RESOURCE", {
                    "task_id": task_id, "attempt": att, "retry_after": retry_after,
                    "commit": c or commit_atual(wt.caminho),
                    "modelo": modelo, "effort": effort}, self._onda(task_id))
                self.log(f"{task_id}: COTA ESGOTADA — aguardando {espera}s "
                         f"(checkpoint em {c or 'HEAD'})")
                self.store.liberar_worktree(str(wt.caminho))
                return ResumoTask(task_id, "WAITING_RESOURCE", n_tent, esperas,
                                  motivo="CODEX_QUOTA")

            # ---- commit + verificação -------------------------------------------
            c = self.wm.commit(wt.caminho, f"{task_id}: {spec['titulo']} ({agente} t{att})")
            alterados = arquivos_alterados(wt.caminho, base)
            self.store.heartbeat(self.sprint, task_id, att)

            if res.failure_class or res.exit_code != 0:
                fp = retry.fingerprint(task_id, res.failure_class or "UNKNOWN",
                                       res.stderr[:200] or res.stdout[:200], alterados)
                self.store.finalizar_tentativa(
                    self.sprint, task_id, att, status="FAILED", exit_code=res.exit_code,
                    changed_files=alterados, failure_class=res.failure_class or "UNKNOWN",
                    fingerprint=fp)
                historico.append({"attempt": att, "failure_class": res.failure_class
                                  or "UNKNOWN", "exit_code": res.exit_code,
                                  "saida": res.stderr or res.stdout, "arquivos": alterados,
                                  "fingerprint": fp, "motivo": "falha do agente"})
                fps.append(fp)
                # HAQ imediato para falhas que exigem humano; segue o resto
                if res.failure_class in ("PERMISSION_REQUIRED", "SECRET_REQUIRED",
                                         "RED_ACTION_REQUIRED"):
                    hid, _ = haq.abrir_por_falha(
                        self.store, self.sprint, task_id=task_id,
                        failure_class=res.failure_class,
                        detalhe=(res.stderr or res.stdout)[:600], cfg=self.cfg)
                    self.store.bloqueia(self.sprint, task_id, f"{hid}: exige humano")
                    haq.escrever(self.store, self.sprint, self.dir_sprint / "HAQ.md")
                    self.store.liberar_worktree(str(wt.caminho))
                    return ResumoTask(task_id, "BLOCKED", att, esperas,
                                      motivo=f"HAQ {hid}")
                self.store.transicionar(self.sprint, task_id, "RETRY",
                                        "falha do agente")
                continue

            self.store.transicionar(self.sprint, task_id, "VERIFYING", "verificando")
            self._checa_parada("rodar_testes", task_id)
            rt = testrunner.rodar(
                wt.caminho, comando=spec.get("teste"),
                evidencia_dir=self.dir_sprint / "evidence", rotulo=task_id)
            self.log(f"{task_id}: testes exit={rt.exit_code} "
                     f"passed={rt.passed} failed={rt.failed} ({rt.detectado_por})")

            if not rt.passou:
                fp = retry.fingerprint(task_id, "TEST_FAILURE", rt.assinatura, alterados)
                self.store.finalizar_tentativa(
                    self.sprint, task_id, att, status="FAILED", exit_code=rt.exit_code,
                    changed_files=alterados, test_result=rt.to_dict(),
                    failure_class="TEST_FAILURE", fingerprint=fp)
                self.store.transicionar(self.sprint, task_id, "RETRY",
                                        "testes falharam")
                historico.append({"attempt": att, "failure_class": "TEST_FAILURE",
                                  "exit_code": rt.exit_code, "saida": rt.saida,
                                  "arquivos": alterados, "fingerprint": fp,
                                  "motivo": "testes falharam"})
                fps.append(fp)
                continue

            # ---- revisão independente -------------------------------------------
            self.store.transicionar(self.sprint, task_id, "REVIEW", "revisao")
            rv = review.revisar(
                worktree=str(wt.caminho), base=base, task_id=task_id,
                titulo=spec["titulo"], criterios=criterios,
                testes=rt.saida[-3000:], agente_impl=agente, cfg=self.cfg,
                disponiveis=self.disponiveis,
                log_dir=str(self.dir_sprint / "logs"))
            self.log(f"{task_id}: revisao por {rv.revisor} -> {rv.veredito} "
                     f"({len(rv.findings)} findings)")
            if not rv.aprovado:
                fp = retry.fingerprint(task_id, "REVIEW_FAILURE", rv.resumo, alterados)
                self.store.finalizar_tentativa(
                    self.sprint, task_id, att, status="FAILED", exit_code=0,
                    changed_files=alterados, test_result=rt.to_dict(),
                    review_result=rv.to_dict(), failure_class="REVIEW_FAILURE",
                    fingerprint=fp)
                self.store.transicionar(self.sprint, task_id, "RETRY",
                                        f"revisao: {rv.veredito}")
                historico.append({"attempt": att, "failure_class": "REVIEW_FAILURE",
                                  "exit_code": 0,
                                  "saida": json.dumps(rv.findings, ensure_ascii=False),
                                  "arquivos": alterados, "fingerprint": fp,
                                  "motivo": f"revisao {rv.veredito}"})
                fps.append(fp)
                continue

            # ---- sucesso ---------------------------------------------------------
            final = commit_atual(wt.caminho)
            self.store.finalizar_tentativa(
                self.sprint, task_id, att, status="OK", exit_code=0,
                changed_files=alterados, test_result=rt.to_dict(),
                review_result=rv.to_dict(), final_commit=final)
            self.store.transicionar(self.sprint, task_id, "DONE", "testes+revisao ok")
            self.store.checkpoint(self.sprint, "DONE", {
                "task_id": task_id, "commit": final, "attempt": att,
                "testes": rt.to_dict(), "review": rv.to_dict()}, self._onda(task_id))
            self.store.liberar_worktree(str(wt.caminho))
            self.log(f"{task_id}: DONE em {att} tentativa(s) — commit {final[:8]}")
            return ResumoTask(task_id, "DONE", att, esperas, rv.to_dict(),
                              rt.to_dict())

    # ------------------------------------------------------------ o sprint
    def rodar(self, *, parar_em: str | None = None) -> ResultadoSprint:
        t0 = time.time()
        res = ResultadoSprint(sprint=self.sprint)
        self.carregar()
        self.recuperar()

        try:
            for onda_i, onda in enumerate(ordem_topologica(self.dag)):
                for task_id in onda:
                    if parar_em and task_id == parar_em:
                        self.log(f"parada solicitada antes de {task_id}")
                        res.parado_por = f"antes de {task_id}"
                        res.duracao_s = time.time() - t0
                        return res
                    self._checa_parada("selecionar_task", task_id)
                    row = self._row(task_id)
                    if row["estado"] in ("DONE", "INTEGRATED", "BLOCKED"):
                        continue
                    deps = [t for t in self.dag["tasks"] if t["id"] == task_id][0].get("deps", [])
                    faltando = [d for d in deps
                                if self._row(d)["estado"]
                                not in ("DONE", "INTEGRATED")]
                    if faltando:
                        self.store.bloqueia(self.sprint, task_id,
                                            f"depende de {faltando}")
                        res.tasks.append(ResumoTask(task_id, "BLOCKED", 0,
                                                    motivo=f"deps {faltando}"))
                        continue
                    # ---- portão de cota ---------------------------------------
                    # "Cota é espera de recurso, não falha" só valia no papel: a
                    # espera era REGISTRADA e ninguém a RESPEITAVA. O orquestrador
                    # reinvocava o agente antes de retry_after, então as 5h10m de
                    # espera nunca aconteciam de fato — era só um número no banco.
                    pendente = self.store.espera_pendente(self.sprint, task_id)
                    if pendente:
                        faltam = int(pendente - time.time())
                        self.log(f"{task_id}: cota esgotada — aguardando {faltam}s "
                                 f"({faltam / 60:.0f} min) antes de tentar de novo")
                        res.tasks.append(ResumoTask(
                            task_id, "WAITING_RESOURCE", 0,
                            motivo=f"espera de recurso: faltam {faltam}s"))
                        res.aguardando_recurso = True
                        continue
                    # a espera venceu (ou nunca houve): fecha a espera e tenta
                    self.store.resolver_esperas(self.sprint, task_id)
                    rst = self.executar_task(task_id)
                    res.tasks.append(rst)
                    self.store.checkpoint(self.sprint, rst.estado_final,
                                          {"task_id": task_id, "resumo": rst.motivo},
                                          onda_i)
                # ---- integração da onda -------------------------------------
                # D-07: integra a CADA onda, e não uma única vez no fim. É o que
                # faz o worktree da onda seguinte nascer com o código já integrado
                # das dependências (ver _base_do_worktree). Integrar só no fim,
                # com todo worktree partindo da main, foi o que deu 1 task
                # integrada de 10 na primeira noite real: as outras 9
                # reimplementaram o mesmo contrato e colidiram no merge, cinco
                # rodadas seguidas.
                self._checa_parada("integrar")
                res_done = [t for t in self.store.tasks(self.sprint)
                            if t["estado"] == "DONE"]
                if res_done:
                    self.log(f"integrando {len(res_done)} task(s) da onda {onda_i}")
                    self.integrar(res_done)
                    # rede de segurança: main nunca é tocada
                    self.log(f"branch atual do repo: {commit_atual(self.raiz)[:8]} "
                             f"(main intacta — merge automatico proibido)")
            # Nada progrediu por espera de cota: é "volte depois", não falha.
            if res.aguardando_recurso and not res.concluidas:
                res.parado_por = "aguardando recurso (cota)"
        except killswitch.ParadoPorKillSwitch as e:
            self.log(f"PARADO: {e}")
            res.parado_por = str(e)
        except Exception as e:  # noqa: BLE001
            tb = traceback.format_exc()
            self.log(f"ERRO NAO TRATADO: {e}\n{tb}")
            haq.abrir(self.store, self.sprint, task_id="(sprint)",
                      reason=f"Erro não tratado no orquestrador: {e}",
                      risk="alto", dependencia="sprint",
                      acao_humana="# inspecionar .autodev/sprints/.../logs/orquestrador.log",
                      resultado="Sprint volta a progredir",
                      verificacao="# tail -50 .autodev/sprints/.../logs/orquestrador.log")
            res.parado_por = f"erro: {e}"
        finally:
            res.duracao_s = time.time() - t0
            self.store.checkpoint(self.sprint, "FIM", {"parado_por": res.parado_por})
            haq.escrever(self.store, self.sprint, self.dir_sprint / "HAQ.md")
        return res

    def integrar(self, tasks_done) -> None:
        integ = integration.Integrador(
            self.raiz, self.cfg.branch_integracao(self.sprint), self.sprint)
        wt_int = integ.garantir_worktree()
        # Comando de teste para os portões de integração.
        #
        # Sem informar, o portão cai na AUTO-DETECÇÃO e devolve "nenhum" quando o
        # projeto não casa com os detectores (sem pyproject.toml, sem pytest.ini e
        # sem tests/ na raiz) — aí o runner sai com 127 e a integração reprova
        # SEMPRE, por falta de comando e não por teste vermelho. Bug real,
        # encontrado no primeiro backlog multi-task: cada task passava nos seus
        # testes e a integração inteira era rejeitada.
        comandos: dict[str, str] = {}
        cmd_teste = (self.sprint_yaml.get("aceitacao") or {}).get("comando")
        if not cmd_teste:
            for t in self.dag.get("tasks", []):
                if t.get("teste"):
                    cmd_teste = t["teste"]
                    break
        if cmd_teste:
            comandos["testes"] = cmd_teste
        else:
            self.log("AVISO: sprint sem comando de teste declarado — o portão de "
                     "integração vai depender da auto-detecção")
        for t in tasks_done:
            r = integ.merge_task(t["task_id"], t["branch"], wt_int)
            if not r.merge_ok:
                self.log(f"integracao {t['task_id']}: CONFLITO — devolvido para retry")
                self.store.forcar_estado(self.sprint, t["task_id"], "RETRY",
                                         f"conflito de merge: {r.conflito[:200]}")
                continue
            portoes = integ.rodar_portoes(wt_int, comandos=comandos,
                                          evidencia_dir=self.dir_sprint / "evidence")
            for p in portoes:
                self.log(f"  portao {p.nome}: {'OK' if p.ok else 'FALHOU'}")
            if all(p.ok for p in portoes):
                self.store.transicionar(self.sprint, t["task_id"], "INTEGRATED",
                                        "merge + portoes ok")
                self.store.evento(self.sprint, t["task_id"], "integrado",
                                  r.to_dict())
            else:
                falhos = [p.nome for p in portoes if not p.ok]
                self.store.forcar_estado(self.sprint, t["task_id"], "RETRY",
                                         f"portoes falharam: {falhos}")
                self.log(f"integracao {t['task_id']}: portoes falharam {falhos}")
