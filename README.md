# Coding Machine

Fábrica de desenvolvimento autônomo: um orquestrador que pega uma especificação
de sprint, quebra em tarefas, executa cada uma com um agente de código dentro de
um worktree isolado, valida com testes determinísticos, faz revisão por um
**agente diferente** do que escreveu, e só então integra.

```
SPEC → PLAN → DAG DE TAREFAS → WORKTREE → AGENTE DE CÓDIGO → TESTE
     → REVISÃO INDEPENDENTE → CORREÇÃO → RETESTE → INTEGRAÇÃO
     → VALIDAÇÃO FINAL → RELATÓRIO
```

Uma tarefa aprovada roda de ponta a ponta sem intervenção humana.

---

## Por que não é só "chamar o Codex num loop"

Porque o valor está no que acontece **quando dá errado**. O projeto trata como
requisito de primeira classe:

- **Quem escreve não revisa.** A revisão é feita por outro agente (Codex escreve,
  AGY revisa, ou o inverso). Auto-revisão não encontra o erro de quem escreveu.
- **Cota de API não é falha.** Esgotar a cota do Codex vira espera de recurso,
  com checkpoint e retomada por prompt de **continuação** — não reinício.
- **Retomada após queda.** Cada tentativa persiste agente, modelo, branch,
  worktree, sandbox, commit base, arquivos alterados, resultado de teste e de
  revisão. O processo pode morrer e voltar de onde parou.
- **Isolamento de verdade.** Cada agente roda em `git worktree` próprio, dentro de
  um sandbox `bubblewrap` sem rede de escape e com HOME efêmero.
- **Nunca integra em `main`.** O sprint produz uma branch
  `sprint/<id>/integration`, com portões de validação antes do merge.

---

## Estrutura

```
.
├── autodev/                    # o orquestrador
│   ├── orchestrator.py         # laço principal, máquina de estados, kill switch
│   ├── state.py                # store SQLite (fonte autoritativa)
│   ├── agents.py               # adaptadores dos agentes + wrapper headless
│   ├── sandbox.py              # isolamento com bubblewrap
│   ├── worktree.py             # git worktree por tarefa
│   ├── testrunner.py           # runner determinístico (pytest)
│   ├── review.py               # revisor cross-agent
│   ├── retry.py                # política de retry e escalada de modelo
│   ├── integration.py          # branch de integração + portões
│   ├── haq.py                  # fila de ação humana
│   ├── report.py               # relatório do sprint
│   ├── killswitch.py           # parada de emergência
│   ├── errors.py               # taxonomia de falhas
│   └── cli.py                  # interface de linha de comando
│
└── .autodev/                   # configuração e estado
    ├── config/                 # agents.yaml, models.yaml, policies.yaml
    ├── sprints/<id>/           # sprint.yaml, dag.json, evidence/, logs/
    ├── fixtures/               # projeto-fixture que COMEÇA QUEBRADO
    ├── tests/                  # suíte do próprio orquestrador
    └── scripts/                # aceitação ponta a ponta com agentes reais
```

---

## Como rodar

Requisitos: Python 3.11+, `git`, e `bubblewrap` (`bwrap`).

```bash
python3 -m venv .venv && .venv/bin/pip install pytest pyyaml

.venv/bin/python -m autodev detect          # quais agentes estão instalados
.venv/bin/python -m autodev init            # valida o DAG do sprint
.venv/bin/python -m autodev status          # estado das tarefas
.venv/bin/python -m autodev run             # executa o sprint
.venv/bin/python -m autodev report          # gera o relatório
.venv/bin/python -m autodev resume          # retoma após interrupção
.venv/bin/python -m autodev haq             # fila de ação humana
```

Testes do próprio orquestrador:

```bash
.venv/bin/python -m pytest .autodev/tests/ -q
```

---

## Os agentes

Configurados em `.autodev/config/agents.yaml`. O orquestrador **detecta** o que
está instalado e usa o que existir — nada é presumido.

| Papel | Agente | Adaptador |
|---|---|---|
| Implementação primária | Codex CLI | `ask-codex` |
| Implementação secundária e revisor independente | Antigravity CLI | `ask-agy` |

O roteamento de modelo por fase e a escada de escalada estão em
`.autodev/config/models.yaml`. Quando uma tarefa falha, o orquestrador sobe o
modelo e o esforço antes de tentar de novo com o mesmo.

Os wrappers `ask-codex` / `ask-agy` são a camada headless que faz o agente
responder sem terminal interativo. Eles não fazem parte deste repositório.

---

## Estado atual

- **Suíte do orquestrador: 106 testes passando.**
- **Aceitação ponta a ponta com agentes reais: verde.** O sprint de teste roda
  sobre o projeto-fixture com o Codex de verdade, 8 testes passam, a revisão
  cruzada devolve apontamentos, a integração passa os 4 portões e a `main`
  fica intacta (mesmo commit antes e depois).
- **O sprint `DEVFACTORY-001` (o backlog que construiu este orquestrador) ainda
  não foi executado pelo próprio orquestrador.** As 15 tarefas estão registradas
  e em estado `NEW`. Falta rodar, gerar o relatório e commitar a evidência.

O projeto-fixture é gerado e não é versionado:

```bash
python3 .autodev/fixtures/criar_fixture.py
```

---

## Segurança

- O diretório `.autodev/sandbox-home/` é uma cópia do HOME criada para o sandbox
  e **contém credencial de agente**. Está no `.gitignore` e não deve ser
  versionado em nenhuma circunstância.
- A integração tem portão de segredo: diferenças que introduzam chaves, tokens ou
  chave privada são barradas antes do merge.
- O sandbox não expõe `~/.ssh`, nem o HOME real, nem as configurações do usuário.
