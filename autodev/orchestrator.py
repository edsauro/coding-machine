"""DEVFACTORY — orquestrador de longo horizonte (spec §5/§7/§21/§30).

Padrão de operação:
    RESTORE STATE -> SELECT NEXT BOUNDED TASK -> EXECUTE -> VERIFY -> CHECKPOINT
    -> SELECT NEXT TASK

O estado autoritativo é Git + SQLite + arquivos do sprint + evidência de teste.
Nunca a memória conversacional.
"""
from __future__ import annotations

import json
import hashlib
import os
import signal
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import agents, errors, haq, integration, killswitch, report, retry, review
from . import sandbox as sbx
from . import testrunner
from .config import (SPRINT_TERMINAIS, Config, carrega_dag, carrega_sprint,
                     ordem_topologica)
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

## Modo headless — leia antes de comecar
- Voce roda SEM HUMANO disponivel: ninguem vai responder pergunta nenhuma.
- NAO peca aprovacao de design nem pergunte "posso implementar?": entregar apenas
  um plano ou uma pergunta conta como FALHA da task, porque nenhum arquivo muda.
- Decida sozinho dentro do que a task e os criterios pedem e implemente agora.

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


class PlanoInvalido(ValueError):
    """Arquivos do plano não puderam ser carregados ou validados."""


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
        # Agentes que estouraram cota NESTA rodada. Regra do autor (2026-09-27):
        # não parar por agente que tem substituto — cota do agente secundário não
        # estaciona a task por 5h10m.
        self._sem_cota: set[str] = set()
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

    def _modelo_da_tentativa(self, d, n_tent: int) -> dict:
        """Degrau de modelo da PRÓXIMA tentativa.

        Sem decisão (1ª tentativa), vale o contador da task (`n_tent + 1`) — chumbar 1
        fazia uma sprint retomada voltar ao degrau barato, ignorando o desbloqueio.
        Com decisão, quem manda é o `tier` DELA: quando a classe de falha não recebe
        escalonamento (P-09 — cota, rede, ambiente, permissão, segredo), a decisão
        carrega o degrau ATUAL e a infra deixa de pagar a chamada cara.
        """
        if d is None:
            return self.cfg.modelo_para_tentativa(n_tent + 1)
        if d.tier:
            atual = self.cfg.degrau_por_tier(d.tier)
            if atual is not None:
                return atual
        return self.cfg.modelo_para_tentativa(d.tentativa_proxima)

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
    def _ler_plano(self) -> None:
        """Lê e valida os arquivos do plano sem materializá-los no estado."""
        self.sprint_yaml = carrega_sprint(self.dir_sprint / "sprint.yaml")
        self.dag = carrega_dag(self.dir_sprint / "dag.json")

    def carregar(self) -> None:
        self._ler_plano()
        novas = self.store.criar_tasks_do_dag(self.sprint, self.dag)
        self.log(f"sprint {self.sprint}: {len(self.dag['tasks'])} tasks "
                 f"({novas} novas) — ondas {ordem_topologica(self.dag)}")

    def _validar_aprovacao(self) -> str | None:
        """Impede executar plano ainda não aprovado ou cujo DAG mudou."""
        estado = self.store.estado_sprint(self.sprint)
        aprovacao = self.sprint_yaml.get("aprovacao")
        comando = f"autodev aprovar {self.sprint} --por <nome>"
        if not isinstance(aprovacao, dict):
            if estado in (None, "PLANEJADO"):
                return f"sprint PLANEJADO sem aprovacao registrada; use {comando}"
            # Sprints iniciados antes da existência do portão continuam retomáveis.
            return None
        if estado in SPRINT_TERMINAIS:
            return None
        atual = hashlib.sha256((self.dir_sprint / "dag.json").read_bytes()).hexdigest()
        registrado = aprovacao.get("hash_dag")
        if registrado != atual:
            return (f"aprovacao invalida: divergencia no hash do dag.json "
                    f"(aprovado {registrado}, atual {atual}); use {comando}")
        return None

    def checar_aprovacao(self) -> str | None:
        """Checa o portão sem criar tasks ou alterar o estado persistido."""
        try:
            self._ler_plano()
        except ValueError as exc:
            raise PlanoInvalido(str(exc)) from exc
        return self._validar_aprovacao()

    def _registrar_aprovacao(self) -> None:
        aprovacao = self.sprint_yaml["aprovacao"]
        hash_dag = aprovacao["hash_dag"]
        eventos = self.store.conn.execute(
            "SELECT payload FROM events WHERE sprint_id=? AND tipo='aprovacao_plano'",
            (self.sprint,),
        ).fetchall()
        for evento in eventos:
            try:
                if json.loads(evento["payload"]).get("hash_dag") == hash_dag:
                    return
            except (TypeError, ValueError):
                continue
        self.store.evento(self.sprint, None, "aprovacao_plano", dict(aprovacao))

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
    def _quarentena_cota(self, agente: str, task_id: str = "") -> None:
        """Tira um agente secundário da rodada quando a cota dele estoura.

        Marca a indisponibilidade em `self.disponiveis`, que é o que a escada de
        revisão e a troca de agente consultam. Sem isto, cada task tentaria o agy
        de novo (minutos por chamada) só para redescobrir a mesma cota estourada.
        """
        if agente in self._sem_cota:
            return
        self._sem_cota.add(agente)
        info = self.disponiveis.get(agente)
        if info is not None:
            info.disponivel = False
            info.erro = f"{info.erro} | cota esgotada nesta rodada".strip(" |")
        self.log(f"{task_id or '-'}: {agente} FORA desta rodada (cota esgotada)")

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
        if getattr(wt, "realinhado", False):
            self.log(f"{task_id}: worktree realinhado para a integracao atual "
                     f"(base antiga {wt.base_antiga[:8] or '(vazia)'}) — a task estava "
                     f"nascendo sem o codigo das dependencias ja integradas (D-18)")
            self.store.evento(self.sprint, task_id, "worktree_realinhado",
                              {"base_antiga": wt.base_antiga, "base_nova": wt.base_commit})
        historico: list[dict] = []
        esperas = 0

        while True:
            self._checa_parada("invocar_agente", task_id)
            r = self._row(task_id)
            n_tent = r["tentativas"]

            if n_tent >= self.cfg.policies["retry"]["max_tentativas_implementacao"]:
                # Defensivo: nunca bloquear uma task que TEM tentativa viva (outro
                # processo). Foi assim que a P04 foi bloqueada no meio da própria
                # revisão, em 28/09, e o run morreu com BLOCKED -> DONE.
                if self._tentativa_viva(task_id):
                    self.log(f"{task_id}: limite de tentativas atingido, mas ha "
                             f"tentativa VIVA desta task — nao bloqueio")
                    return ResumoTask(task_id, "RUNNING", n_tent, esperas,
                                      motivo="tentativa viva em outro processo")
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
                    alvo = d.agente
                    if alvo in self._sem_cota:
                        # Trocar para quem já estourou cota nesta rodada custaria
                        # 5h10m de espera por um agente SECUNDÁRIO. Mantém quem
                        # está funcionando — a sprint não para por revisor/agente
                        # que tem substituto.
                        self.log(f"{task_id}: {alvo} esta fora desta rodada (cota)"
                                 f" — seguindo com {agente}")
                        alvo = agente
                    if alvo != agente:
                        agente = alvo
                        wt = self.wm.criar(self.sprint, task_id, agente, base=base)
                        self.store.adquirir_worktree(str(wt.caminho), task_id, agente)
                modelo_info = self._modelo_da_tentativa(d, n_tent)
            else:
                d = None
                # O modelo da tentativa vem do CONTADOR DA TASK (n_tent + 1), não de
                # um "1" fixo: numa retomada o contador pode já estar em 2 (a próxima
                # tentativa é a 3ª da escada) — chumbar 1 fazia a sprint retomada
                # voltar para o degrau mais barato, ignorando o desbloqueio.
                modelo_info = self._modelo_da_tentativa(None, n_tent)

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

            # ---- consumo MEDIDO da chamada (P-10) -------------------------------
            # O número vem do wrapper (rodapé "tokens used" capturado antes de o
            # mktemp morrer) ou de um rodapé que tenha vazado no stdout. Quando não
            # há fonte, a coluna fica NULL e o log diz "nao medido" — estimar aqui
            # contaminaria qualquer comparação de custo entre modelos.
            if self.store.gravar_tokens(self.sprint, task_id, att,
                                        res.tokens_total, res.tokens_fonte):
                self.log(f"{task_id} t{att}: {res.tokens_total:,} tokens "
                         f"({res.tokens_fonte})")

            # ---- cota do Codex: espera de RECURSO, não falha --------------------
            if res.failure_class == "CODEX_QUOTA":
                if agente != "codex":
                    # A espera de 5h10m protege a cota do agente PRIMÁRIO. Cota do
                    # agente secundário (agy) não pode estacionar a task: o agy sai
                    # da rodada, a tentativa é DEVOLVIDA (não conta) e o codex
                    # reassume. `continue` reexecuta com quem funciona.
                    self._quarentena_cota(agente, task_id)
                    self.store.finalizar_tentativa(
                        self.sprint, task_id, att, status="WAITING_RESOURCE",
                        exit_code=res.exit_code, failure_class="QUOTA_AGENTE",
                        test_result={"nota": f"cota do {agente} esgotada: agente fora"
                                             " desta rodada, tentativa devolvida"})
                    self.store.conn.execute(
                        "UPDATE tasks SET tentativas=?, agente='codex', estado='QUEUED',"
                        " atualizado_em=? WHERE sprint_id=? AND task_id=?",
                        (n_tent, time.time(), self.sprint, task_id))
                    self.store.conn.commit()
                    self.store.liberar_worktree(str(wt.caminho))
                    # a variável local também muda: só mexer no banco faria o laço
                    # reexecutar o MESMO agente sem cota e girar para sempre.
                    agente = "codex"
                    continue
                espera = self.cfg.espera_cota(self.modo_teste)
                # A política é um chute (5h10m). O agente costuma dizer quando a cota
                # volta: nesta madrugada o codex avisou "try again at 9:10 AM" e o
                # motor dormiu 5h por cima; na vez anterior, 56 min a mais. O reset
                # informado vale só quando é MENOR que a política — a política segue
                # como teto, para que um parse absurdo não estacione o sprint.
                efetiva = errors.espera_efetiva(
                    espera, f"{res.stdout or ''}\n{res.stderr or ''}")
                if efetiva != espera:
                    self.log(f"{task_id}: o agente informou o reset da cota em "
                             f"{efetiva}s ({efetiva / 60:.0f} min); a política era "
                             f"{espera}s")
                    espera = efetiva
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
                commit_ck = c or commit_atual(wt.caminho)
                self.store.checkpoint(self.sprint, "WAITING_RESOURCE", {
                    "task_id": task_id, "attempt": att, "retry_after": retry_after,
                    "commit": commit_ck,
                    "modelo": modelo, "effort": effort}, self._onda(task_id))
                self.log(f"{task_id}: COTA ESGOTADA — aguardando {espera}s "
                         f"(checkpoint em {str(commit_ck)[:8]})")
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

            # ---- entrega VAZIA: descoberta AQUI, não na revisão ------------------
            # Agente que responde com pergunta de design/pedido de aprovacao nao
            # entregou nada: o worktree fica igual ao base e a suite pre-existente
            # continua VERDE — o que faria a revisão parecer saudavel e queimaria
            # minutos de revisor para descobrir o obvio. Fica DEPOIS dos testes de
            # proposito: se a suite esta vermelha, TEST_FAILURE explica melhor.
            if not alterados and not res.failure_class and res.exit_code == 0:
                fp = retry.fingerprint(task_id, "SEM_ENTREGA", (res.stdout or "")[-200:], [])
                self.store.finalizar_tentativa(
                    self.sprint, task_id, att, status="FAILED", exit_code=0,
                    changed_files=[], failure_class="SEM_ENTREGA", fingerprint=fp,
                    test_result=rt.to_dict())
                historico.append({
                    "attempt": att, "failure_class": "SEM_ENTREGA", "exit_code": 0,
                    "saida": "A tentativa anterior NAO alterou nenhum arquivo. "
                             "Resposta final do agente:\n" + (res.stdout or "")[-1500:],
                    "arquivos": [], "fingerprint": fp, "motivo": "entrega vazia"})
                fps.append(fp)
                self.store.transicionar(self.sprint, task_id, "RETRY", "entrega vazia")
                self.log(f"{task_id}: SEM ENTREGA — nenhum arquivo alterado e suite "
                         f"verde; resposta final do agente: "
                         f"\"{(res.stdout or '').strip()[:120]}\"")
                continue

            # ---- revisão independente -------------------------------------------
            self.store.transicionar(self.sprint, task_id, "REVIEW", "revisao")
            rv = review.revisar(
                worktree=str(wt.caminho), base=base, task_id=task_id,
                titulo=spec["titulo"], criterios=criterios,
                testes=rt.saida[-3000:], agente_impl=agente, cfg=self.cfg,
                disponiveis=self.disponiveis,
                log_dir=str(self.dir_sprint / "logs"), tentativa=n_tent + 1)
            self.log(f"{task_id}: revisao por {rv.revisor} "
                     f"[{rv.origem or 'cruzada'}] -> {rv.veredito} "
                     f"({len(rv.findings)} findings)")
            for ag_sem_cota in (rv.sem_cota or []):
                self._quarentena_cota(ag_sem_cota, task_id)
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
    def _batimento(self, parar: threading.Event) -> None:
        """Renova os locks enquanto a rodada vive.

        Sem isto, o heartbeat do writer_lock fica no instante do início da tentativa
        e uma revisão de LLM (5 min) parece "rodada morta" para qualquer observador
        externo — guarda, tela, vigia de cron.
        """
        while not parar.wait(20):
            try:
                self.store.renovar_lock(self.sprint)
            except Exception:  # noqa: BLE001 — batimento nunca derruba a rodada
                pass

    def _tentativa_viva(self, task_id: str, janela_s: float = 1800) -> bool:
        """Existe tentativa RUNNING recente desta task (possivelmente de outro processo)?"""
        r = self.store.conn.execute(
            "SELECT start_time FROM attempts WHERE sprint_id=? AND task_id=?"
            " AND status='RUNNING' ORDER BY start_time DESC LIMIT 1",
            (self.sprint, task_id)).fetchone()
        return bool(r and (time.time() - float(r["start_time"] or 0)) < janela_s)

    def rodar(self, *, parar_em: str | None = None) -> ResultadoSprint:
        t0 = time.time()
        res = ResultadoSprint(sprint=self.sprint)
        bloqueio_aprovacao = self.checar_aprovacao()
        if bloqueio_aprovacao:
            self.log(f"RODADA NAO INICIADA: {bloqueio_aprovacao}")
            res.parado_por = bloqueio_aprovacao
            res.duracao_s = time.time() - t0
            return res
        self.carregar()
        self.recuperar()

        # ---- rodada única por sprint -------------------------------------------
        # Quem impede rodada dupla é o MOTOR. A versão anterior deixava isso para
        # fora (guarda por pgrep/heartbeat) e pagou caro: em 28/09 um vigia de cron
        # disparou outra rodada enquanto a primeira revisava uma task por 5 min;
        # ela integrou a P01 no meio da revisão da P04, o portão de segurança deu
        # falso positivo e a P04 foi bloqueada por limite — o run morreu com
        # TransicaoInvalida: BLOCKED -> DONE.
        ok_lock, motivo_lock = self.store.adquirir_run_lock(self.sprint)
        if not ok_lock:
            self.log(f"RODADA NAO INICIADA: {motivo_lock}")
            res.parado_por = motivo_lock
            res.duracao_s = 0.0
            return res
        if isinstance(self.sprint_yaml.get("aprovacao"), dict):
            self._registrar_aprovacao()
        self.log(f"rodada dona: pid {os.getpid()} — rodada única garantida pelo motor")
        parar_batimento = threading.Event()
        fio = threading.Thread(target=self._batimento, args=(parar_batimento,),
                               daemon=True)
        fio.start()

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
            parar_batimento.set()
            fio.join(timeout=3)
            self.store.liberar_run_lock(self.sprint)
            res.duracao_s = time.time() - t0
            self.store.checkpoint(self.sprint, "FIM", {"parado_por": res.parado_por})
            haq.escrever(self.store, self.sprint, self.dir_sprint / "HAQ.md")
        return res

    def rearmar_dependentes(self) -> list[str]:
        """Reabre tasks bloqueadas SÓ por dependência que já foi integrada.

        Uma passada do laço percorre as ondas UMA vez: quando uma task volta para
        RETRY (conflito de merge, por exemplo) as dependentes ficam BLOCKED e a
        rodada termina — foi assim que a primeira noite fechou com 9 tasks
        bloqueadas. Aqui BLOCKED não foi decisão humana, foi ordem de execução.
        Devolver essas tasks para QUEUED — preservando o contador da escada, ao
        contrário de `desbloquear`, que rearma por decisão do autor — deixa a
        rodada seguinte continuar de onde parou, sem comando na mão.
        """
        deps = {t["id"]: list(t.get("deps", [])) for t in self.dag.get("tasks", [])}
        prontas = {t["task_id"] for t in self.store.tasks(self.sprint)
                   if t["estado"] in ("DONE", "INTEGRATED")}
        rearmadas: list[str] = []
        for t in self.store.tasks(self.sprint):
            if t["estado"] != "BLOCKED":
                continue
            if not (t["bloqueio"] or "").startswith("depende de"):
                continue
            faltando = [d for d in deps.get(t["task_id"], []) if d not in prontas]
            if faltando:
                continue
            self.store.forcar_estado(self.sprint, t["task_id"], "QUEUED",
                                     "dependencia integrada: rearmada automaticamente")
            self.store.evento(self.sprint, t["task_id"], "rearmado_por_dependencia",
                              {"deps_integradas": deps.get(t["task_id"], [])})
            rearmadas.append(t["task_id"])
        return rearmadas

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
            mudados = integ.arquivos_do_merge(wt_int, r.commit)
            portoes = integ.rodar_portoes(wt_int, comandos=comandos,
                                          evidencia_dir=self.dir_sprint / "evidence",
                                          arquivos_mudados=mudados)
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
