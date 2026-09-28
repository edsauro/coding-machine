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
.venv/bin/python -m autodev encerrar        # fecha o sprint (estado terminal)
.venv/bin/python -m autodev evidenciar T03 -e "autodev/state.py + 8 testes"
```

Testes do próprio orquestrador:

```bash
.venv/bin/python -m pytest .autodev/tests/ -q
```

## Planejador

O planejador transforma um prompt em texto livre em um sprint executável. Ele
envia o pedido a um agente, interpreta e valida o plano devolvido, ordena as
tarefas por dependência e materializa os arquivos que o orquestrador consome:
`spec.md`, `dag.json` e `sprint.yaml`.

Por exemplo:

```bash
.venv/bin/python -m autodev plan "Adicionar autenticação à API"
```

O comando cria um novo diretório `.autodev/sprints/DEVFACTORY-NNN/` no disco,
contendo os três arquivos acima. Como o planejador usa um agente, cada execução
consome cota do agente configurado. O plano gerado **sempre precisa de revisão
humana antes de rodar**: a validação estrutural não garante que a interpretação
do pedido ou os critérios de aceitação estejam corretos.

`plan` somente prepara os arquivos locais do sprint; ele **não faz merge nem
push**.

### Ciclo de vida do sprint

Tarefas têm 12 estados; o **sprint** tem os seus:

```
PLANEJADO → EM_EXECUCAO → EM_VERIFICACAO → ENCERRADO
                  ↘              ↘
                            ABORTADO
```

`encerrar` **recusa** se houver tarefa fora de estado terminal — encerrar com
trabalho em aberto é justamente o que o HAQ existe para evitar. `--forcar` aborta
um sprint travado e grava o motivo.

A fonte autoritativa é `state.db`. O `status:` do `sprint.yaml` é declaração de
intenção, não fato.

### Conclusão por evidência

Quando o trabalho de uma tarefa foi feito **fora** do laço do orquestrador, ele
não pode ser registrado como se o orquestrador tivesse executado — isso seria
evidência falsa.

```bash
.venv/bin/python -m autodev evidenciar T03 -e "descrição do artefato verificável"
```

O comando percorre o caminho válido de transições e marca a tentativa com
**`origem='retroativo'`**. O relatório mostra essa distinção e alerta quando
existe conclusão que não veio do laço.

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

- **Suíte do orquestrador: 123 testes passando.**
- **Sprint `DEVFACTORY-001`: ENCERRADO** (`EM_EXECUCAO → EM_VERIFICACAO →
  ENCERRADO`). As 15 tarefas estão concluídas e o relatório está em
  `.autodev/sprints/DEVFACTORY-001/SPRINT-REPORT.md`.
- **Como as tarefas foram concluídas — leia antes de confiar no número:** as 14
  primeiras (T01–T14) foram concluídas por **evidência retroativa**. O código que
  elas descrevem foi escrito durante o desenvolvimento, **fora** do laço do
  orquestrador. A tentativa existe, o artefato é verificável e os testes passam,
  mas **não foi o orquestrador que executou**. Só a T15 (aceitação autônoma) roda
  de verdade. O banco marca cada caso com `origem` e o relatório diz isso na
  primeira seção.
- **Aceitação ponta a ponta com agentes reais: verde.** Executada em ~2,5 min:
  o Codex implementou o projeto-fixture, os 8 testes passaram, o **AGY revisou e
  aprovou** (1 apontamento), os portões de integração passaram e a `main` ficou
  intacta (mesmo commit antes e depois).
- **Dois itens abertos no HAQ** (`.autodev/sprints/DEVFACTORY-001/HAQ.md`).

O projeto-fixture é gerado e não é versionado:

```bash
.venv/bin/python .autodev/fixtures/criar_fixture.py
```

---

## Segurança

- O diretório `.autodev/sandbox-home/` é uma cópia do HOME criada para o sandbox
  e **contém credencial de agente**. Está no `.gitignore` e não deve ser
  versionado em nenhuma circunstância.
- **Há um hook de commit como segunda barreira** (`.githooks/pre-commit`): ele
  barra caminhos proibidos e segredos no conteúdo, mesmo com `git add -f`. Ative
  uma vez por clone:

  ```bash
  git config core.hooksPath .githooks
  ```

  O hook nunca imprime o valor do segredo que encontrou — só o arquivo, a linha e
  o tipo, porque uma mensagem de erro que mostra o token vaza o token para o log.
- A integração tem portão de segredo: diferenças que introduzam chaves, tokens ou
  chave privada são barradas antes do merge.
- O sandbox não expõe `~/.ssh`, nem o HOME real, nem as configurações do usuário.
