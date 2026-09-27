# Sprint DEVFACTORY-001 — Especificação

**Objetivo:** construir e validar o **loop de código autônomo mínimo** — um
sistema que pega uma especificação de sprint, quebra em tarefas, executa cada
uma com um agente de código real dentro de um worktree isolado, roda testes
determinísticos, faz revisão independente, integra numa branch de sprint e
produz um relatório auditável.

**Resultado do Sprint 1:** o orquestrador funcionando ponta a ponta **sem
merge em produção** e **sem intervenção humana** no caminho feliz.

---

## 1. Problema

Agentes de código autônomos falham de forma silenciosa e não auditável:

| Sintoma | Causa raiz |
|---|---|
| "Concluiu", mas nada mudou | sucesso silencioso: exit 0 + stdout vazio |
| Mesma falha repetida em loop | retry sem evidência nova, sem fingerprint de falha |
| Estado perdido no meio | estado só na memória do processo |
| Agente altera o repositório real | sem isolamento de filesystem |
| Revisão carimbada | quem implementa revisa a si mesmo |
| Relatório inventado | relatório escrito de memória, não derivado dos fatos |

O Sprint 1 ataca exatamente esses seis pontos.

---

## 2. Escopo

### Entra

- Detecção real dos agentes instalados (binário + versão + wrapper headless)
- Store de estado durável em SQLite com máquina de estados validada
- Isolamento por worktree git, um por tarefa
- Sandbox de execução **sem root** (bubblewrap)
- Adaptadores para **Codex** e **Antigravity/AGY**
- Runner de testes determinístico com assinatura estável de falha
- Revisão cruzada: **quem implementa nunca revisa o próprio trabalho**
- Retry com escalonamento de modelo e troca de agente
- Fila de Ação Humana (HAQ) para o que exige gente
- Branch de integração do sprint com portões em ordem
- Relatório final derivado do banco, em 16 seções

### Não entra (não-objetivos)

- **Merge automático em `main` ou produção.** Nunca. O sprint termina numa
  branch de integração e para.
- Interface gráfica, servidor, API HTTP
- Múltiplos repositórios por sprint
- Paralelismo real entre ondas (o DAG declara o que é paralelizável, mas o
  Sprint 1 executa sequencialmente — a ordem topológica existe para evitar
  colisão, não para concorrer)
- Suporte a Windows/macOS
- Custo em dinheiro como métrica de sucesso

---

## 3. Arquitetura

```
autodev/
  config.py        schemas, máquina de estados, DAG, políticas, kill switch
  state.py         StateStore — SQLite, fonte autoritativa de tudo
  agents.py        detecção + adaptadores (codex, agy) e wrappers headless
  worktree.py      um worktree git por tarefa; base_commit registrado
  sandbox.py       bubblewrap: worktree RW, sistema RO, HOME real ausente
  testrunner.py    detecção do comando de teste, execução, assinatura de falha
  review.py        portões objetivos + revisor cruzado por LLM
  retry.py         fingerprint, escalonamento, troca de agente, bloqueio
  haq.py           Fila de Ação Humana — geração do HAQ.md
  integration.py   branch de integração e os portões
  report.py        relatório de 16 seções a partir do state.db
  killswitch.py    parada por arquivo e por banco, 4 níveis
  orchestrator.py  o laço: onda → task → tentativa → portões → integração
  cli.py           interface de linha de comando
  errors.py        taxonomia de falhas
```

### Princípios de projeto

1. **O banco é a fonte autoritativa.** O `status:` do `sprint.yaml` declara
   intenção; o fato está em `state.db`. Relatório sai do banco, nunca da
   memória de quem rodou.
2. **Falha alta e cedo.** DAG inválido, transição inválida, schema inválido —
   tudo barra antes de gastar agente.
3. **Idempotência.** Recriar task, criar worktree, adicionar item de HAQ,
   registrar checkpoint: rodar de novo não duplica nada.
4. **Simplicidade acima de infraestrutura.** Sem daemon, sem root, sem serviço.

---

## 4. Máquina de estados

### Tarefa — 12 estados

```
NEW → PLANNED → QUEUED → RUNNING → VERIFYING → REVIEW → DONE → INTEGRATED
                              ↓         ↓         ↓
                           FAILED    RETRY    BLOCKED
                              ↓         ↓
                           QUEUED ←────┘
```

Transições válidas são explícitas e uma transição não declarada **levanta
erro** — não existe "forçar" fora de uma recuperação de crash, que é uma
operação separada e registrada.

### Sprint — 5 estados

```
PLANEJADO → EM_EXECUCAO → EM_VERIFICACAO → ENCERRADO
     ↘           ↘              ↘
                  ABORTADO
```

O ciclo do sprint é distinto do ciclo da tarefa. Sem um estado terminal
explícito, "encerrar o sprint" não existe como operação.

`encerrar_sprint` **recusa** se houver qualquer tarefa fora de estado terminal —
encerrar com trabalho em aberto é justamente o que o HAQ existe para evitar.
`--forcar` existe só para abortar um sprint travado, e grava o motivo.

---

## 5. Requisitos funcionais

| # | Requisito |
|---|---|
| RF-01 | Detectar codex, agy e hermes por binário e versão reais; ausente ≠ erro fatal |
| RF-02 | Validar `sprint.yaml` e `dag.json` na carga; ciclo, id duplicado e dep inexistente falham alto |
| RF-03 | Persistir task, tentativa, evento, HAQ, kill switch e checkpoint em SQLite |
| RF-04 | Um worktree por tarefa, branch `sprint/<sprint>/<task>-<agente>` |
| RF-05 | Invocar agente headless com modelo e effort **explícitos**, persistindo o que foi usado de fato |
| RF-06 | Classificar exaustão de cota como `CODEX_QUOTA`, distinta de falha de implementação |
| RF-07 | Sandbox sem root: worktree RW, sistema RO, HOME real ausente |
| RF-08 | Detectar o comando de teste do projeto e devolver resultado estruturado |
| RF-09 | Calcular assinatura estável de falha (normalizada) para o anti-loop |
| RF-10 | Revisão cruzada obrigatória; portões objetivos de segurança rodam sempre, com ou sem LLM |
| RF-11 | Escalonar modelo após 2 tentativas informadas; nunca escalonar por falha de ambiente |
| RF-12 | Gerar `HAQ.md` com os 7 campos, incluindo comando exato e verificação |
| RF-13 | Portões de integração na ordem: merge → build → testes → lint → segurança → aceitação |
| RF-14 | Gerar relatório de 16 seções a partir do banco |
| RF-15 | Encerrar o sprint só sem tarefa pendente |

## 6. Requisitos não-funcionais

| # | Requisito |
|---|---|
| RNF-01 | Nenhum `sudo`/root em nenhum caminho |
| RNF-02 | Nunca merge automático em `main` |
| RNF-03 | Estado sobrevive a reinicialização do processo |
| RNF-04 | Cota é espera de recurso, **não** falha — não consome tentativa |
| RNF-05 | Caminhos proibidos (`~/.ssh`, `~/.codex/auth.json`, `~/.hermes/config.yaml`) inacessíveis dentro do sandbox, **provado por execução real** |
| RNF-06 | Toda tentativa registra commit base e commit final |
| RNF-07 | Suíte de testes determinística, sem rede, < 60 s |

---

## 7. Critérios de aceitação

O sprint é aceito quando:

1. `python3 -m autodev init` valida o DAG e cria as 15 tarefas
2. `python3 -m autodev run` executa o fluxo completo **sem intervenção humana**
3. Um teste vermelho vira verde (ciclo TDD provado de verdade)
4. A revisão cruzada acontece: **AGY revisa o trabalho do Codex**
5. Exaustão de cota simulada produz `WAITING_RESOURCE`, checkpoint e retomada
   por **CONTINUAÇÃO** (não reinício do zero)
6. A branch de integração recebe o merge; `main` permanece intocada
7. `python3 -m autodev report` gera o relatório de 16 seções
8. A suíte do próprio orquestrador passa integralmente

---

## 8. Restrições

- **Agentes com conta gratuita.** Codex (plano ChatGPT Free/Go) e Antigravity
  (conta Google). Sem API paga.
- **Execução headless.** Os wrappers `ask-codex`/`ask-agy` existem em
  `~/.local/bin`; o protocolo está em `~/.local/share/agent-bridge/PROTOCOL.md`.
- **Arch Linux + Omarchy.** `bwrap` disponível, sem container runtime.
- **Segredos nunca versionados.** `~/.codex/auth.json` é copiado com chmod 600
  para o HOME do sandbox — nunca entra no git.

---

## 9. Riscos conhecidos

| Risco | Mitigação |
|---|---|
| Agente headless "auto-nega" ferramentas e devolve exit 0 | `-D` obrigatório **e** sucesso silencioso tratado como falha |
| Revisor cruzado indisponível | fallback determinístico, registrado como tal |
| Sandbox indisponível na máquina | executa com aviso explícito, registrado na tentativa |
| Loop de retry sem progresso | fingerprint de falha + evidência nova obrigatória |
| Relatório divergir da realidade | relatório derivado do banco, nunca redigido |

---

## 10. Glossário

- **Tentativa (attempt):** uma execução de um agente sobre uma tarefa
- **Onda:** grupo de tarefas sem dependência entre si, executável antes da próxima
- **HAQ:** Human Action Queue — fila do que só uma pessoa pode fazer
- **Portão:** verificação obrigatória que a integração precisa atravessar
- **Sucesso silencioso:** exit 0 com stdout vazio e stderr reclamando — tratado
  como **falha**, não como sucesso
