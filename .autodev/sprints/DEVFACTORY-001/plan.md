# Sprint DEVFACTORY-001 — Plano

**DAG:** 15 tarefas em **6 ondas**. Gerado e validado por
`python3 -m autodev init` — este documento descreve o que o código produz, não
o contrário.

**Critério de paralelização:** tarefas acopladas ficam em ondas separadas.
Paralelizar só quando os arquivos tocados são disjuntos.

---

## Ondas

| Onda | Tarefas | Paralelizável |
|---|---|---|
| 0 | T01, T02 | sim |
| 1 | T03, T04 | não |
| 2 | T05, T06, T07, T08, T11 | sim |
| 3 | T09, T10, T12, T14 | não |
| 4 | T13 | — |
| 5 | T15 | não |

```
onda 0   T01 ─┬─────────────────────────────┐
         T02 ─┼──┬──────────┬───────────────┤
onda 1        │  T03        T04             │
onda 2        │  ├─ T05 ─┬  ├─ T07 ─┐       │
              │  ├─ T06 ─┤  ├─ T08 ─┼─┐     │
              │  └─ T11  │  └───────┘ │     │
onda 3        │     T09 ─┘   T10 ─────┤     │
              │              T12 ─────┤     │
              │              T14 ─────┘     │
onda 4        │              T13 ───────────┤
onda 5        └────────────── T15 ◄─────────┘
```

---

## Tarefas

### Onda 0 — fundação

| ID | Título | Est. | Agente |
|---|---|---|---|
| T01 | Detectar agentes de código instalados | S | codex |
| T02 | Definir schemas de Sprint e de task | S | codex |

**T01** — codex, agy e hermes detectados por binário e versão reais; wrapper
headless verificado, não presumido; agente ausente aparece como indisponível
sem quebrar o sprint; a detecção é persistida como evidência.

**T02** — `sprint.yaml` e `dag.json` com schema validado na carga; DAG inválido
(id duplicado, dep inexistente, ciclo) falha alto e cedo; máquina de 12 estados
declarada com transições explícitas; `ordem_topologica` devolve ondas.

### Onda 1 — persistência e isolamento

| ID | Título | Deps | Est. |
|---|---|---|---|
| T03 | Store de estado em SQLite | T02 | M |
| T04 | Gerente de worktrees Git | T02 | M |

**T03** — schema cobre `tasks`, `attempts`, `events`, `haq`, `killswitch`,
`resource_waits`, `writer_lock`, `sprint_state`. Transição inválida é barrada.
Todas as operações idempotentes. Sobrevive a reabertura do processo.

**T04** — worktree e branch por tarefa (`sprint/<sprint>/<task>-<agente>`);
criação idempotente; `base_commit` registrado; `merge --no-ff` só para o branch
de integração, **nunca** para `main`; conflito detectado sem deixar o repo sujo.

### Onda 2 — adaptadores e verificação

| ID | Título | Deps | Est. |
|---|---|---|---|
| T05 | Adaptador do Codex | T01, T03 | M |
| T06 | Adaptador do Antigravity/AGY | T01, T03 | M |
| T07 | Isolamento de execução (sandbox) | T04 | M |
| T08 | Runner de testes determinístico | T04 | M |
| T11 | Fila de Ação Humana (HAQ) | T03 | S |

**T05** — invoca o Codex headless com modelo e effort explícitos; persiste o que
foi **realmente** usado, sem inventar nomes; log completo em disco; exaustão de
cota classificada como `CODEX_QUOTA`, distinta de falha de implementação.

**T06** — AGY headless em modo plano (somente leitura) para revisão;
indisponibilidade tratada limpo; mesmo formato de resultado do Codex.

**T07** — worktree RW, sistema RO, HOME real **não** montado; caminhos proibidos
(`~/.ssh`, `~/.codex/auth.json`, `~/.hermes/config.yaml`) inacessíveis, provado
por execução real; sem sudo/root.

**T08** — detecta o comando de teste (pytest/unittest/npm/make/go); roda dentro
do sandbox; saida integral salva como evidência; assinatura estável de falha
para o anti-loop; prova o ciclo TDD vermelho→verde.

**T11** — `HAQ.md` com os 7 campos, incluindo comando exato e verificação;
criação idempotente; só as tasks dependentes bloqueiam; falhas de
permissão/segredo/ação vermelha viram item de HAQ automaticamente.

### Onda 3 — controle de qualidade

| ID | Título | Deps | Est. |
|---|---|---|---|
| T09 | Revisor independente entre agentes | T05, T06 | M |
| T10 | Retry e recuperação | T03, T08 | M |
| T12 | Branch de integração do Sprint | T04, T08 | M |
| T14 | Projeto-fixture ponta a ponta | T04, T08 | M |

**T09** — quem implementa nunca revisa o próprio trabalho; codex→agy e
agy→codex; fallback determinístico quando o cruzado falta; veredito estruturado
(APPROVE/REQUEST_CHANGES/REJECT) com findings; portões objetivos de segurança
rodam sempre, com ou sem LLM.

**T10** — fingerprint combina task, classe, assinatura, arquivos e testes
falhos; escalonamento sobe um degrau após 2 tentativas materialmente informadas;
classes de ambiente/dependência/permissão **nunca** escalonam modelo; 4ª falha
repetida troca de agente, 5ª bloqueia; retry sempre com evidência nova; cota não
consome tentativa.

**T12** — branch `sprint/<sprint>/integration`; portões na ordem
merge → build → testes → lint → segurança → aceitação; falha de portão devolve a
task para retry; **nunca** merge automático em `main`.

**T14** — repositório git independente com uma feature delimitada e testes; a
feature começa quebrada (teste vermelho) para provar o ciclo TDD; artefatos do
sprint ficam no repo do orquestrador, não no fixture; o fixture é descartável e
recriável do zero.

### Onda 4 — relatório

| ID | Título | Deps | Est. |
|---|---|---|---|
| T13 | Geração do relatório do Sprint | T03, T12 | M |

Relatório derivado do `state.db`, **não** de memória conversacional; cobre as 16
seções; inclui métricas calculáveis (tasks, tentativas, escalonamentos, esperas
de cota, HAQ); expõe a base para calcular HAR.

### Onda 5 — aceitação

| ID | Título | Deps | Est. |
|---|---|---|---|
| T15 | Executar teste de aceitação autônomo | 9 deps | L |

Fluxo completo sem intervenção humana: plan → worktree → implementação → teste →
revisão → correção → reteste → integração → relatório. Exaustão de cota simulada
resulta em `WAITING_RESOURCE`, checkpoint e retomada com **CONTINUAÇÃO** (não
reinício). Nenhum sudo/root; nenhum merge em `main`; o estado sobrevive a
reinicialização do orquestrador.

---

## Fluxo de execução

```
para cada onda:
    para cada task da onda:
        checkpointer / kill switch / cota
        ├── criar worktree
        ├── montar sandbox
        ├── invocar agente  ──► CODEX_QUOTA? → WAITING_RESOURCE (não conta tentativa)
        ├── rodar testes    ──► vermelho? → fingerprint → retry com evidência nova
        ├── revisão cruzada ──► REQUEST_CHANGES? → volta para implementação
        └── portões ──────────► falha? → retry; ok? → DONE
    integrar a onda no branch sprint/<sprint>/integration
gerar relatório
encerrar sprint (recusa se houver task pendente)
```

## Estratégia de retry

| Tentativa | Ação |
|---|---|
| 1 | mesmo modelo, com a evidência da falha |
| 2 | evidência nova obrigatória (fingerprint diferente) |
| 3 | **escala um degrau** de modelo |
| 4 | **troca de agente** (codex ↔ agy) |
| 5 | **bloqueia** a task e abre item no HAQ |

Exceção: falhas de ambiente, dependência e permissão **nunca** escalonam modelo —
não é problema do modelo, é do ambiente. E exaustão de cota não consome
tentativa: é espera de recurso.

---

## Definição de pronto (por tarefa)

Uma tarefa só está `DONE` quando:

1. o agente devolveu código (não só texto)
2. os testes rodaram **dentro do sandbox** e ficaram verdes
3. a revisão cruzada aprovou, ou os findings foram tratados
4. os portões objetivos de segurança passaram
5. tudo isso está persistido em `attempts` e `events`
