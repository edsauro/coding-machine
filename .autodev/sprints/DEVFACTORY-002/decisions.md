# DEVFACTORY-002 — Decisões

Decisões tomadas durante a Sprint 2 (planejador + primeira execução autônoma
multi-task). Formato herdado da Sprint 1: **Contexto → Fato → Decisão →
Consequência**. Bugs reais marcados com **[bug]**.

---

## D-01 **[bug]** — "cota é espera de recurso" só valia no papel

**Contexto:** a spec §12 declara que cota esgotada é **espera de recurso**, não
falha. O código registrava a espera: gravava `retry_after` em `resource_waits`,
incrementava `esperas_cota` separado de `tentativas` e devolvia
`WAITING_RESOURCE`. Os testes de aceitação passavam.

**Fato:** `esperas_vencidas()` existia mas era usada **apenas para escrever no
log**. Nada consultava `retry_after` antes de invocar o agente, e nada marcava a
espera como resolvida (a coluna `resolvido` nunca virava 1). Na prática o
orquestrador reinvocava o agente antes da hora: as 5h10m de espera nunca
aconteciam — eram só um número no banco.

**Decisão:** portão de verdade. `espera_pendente()` devolve o `retry_after` ainda
no futuro; `resolver_esperas()` fecha a espera quando ela vence. O laço **pula**
a task com espera pendente e reporta `parado_por = "aguardando recurso (cota)"`,
que é "volte depois" e não travamento.

**Consequência:** sem esse portão, um supervisor que retoma a cada 5 minutos
martela a cota a noite inteira. O dado existir não cria o mecanismo.

---

## D-02 **[bug]** — a suíte de testes não podia rodar dentro do sandbox

**Contexto:** o orquestrador roda os testes de cada task **dentro do sandbox**
(`bwrap`). A suíte do próprio Coding_Machine era verde no host — 132 testes.

**Fato:** dentro do sandbox, **2 testes falhavam**, sempre os mesmos, antes de
qualquer alteração do agente: `test_sandbox_esconde_caminhos_proibidos` e
`test_sandbox_home_e_efemero`. Ambos verificam o isolamento **a partir do host** e
usam `Path.home()`. Dentro do sandbox `Path.home()` é `/tmp/home` — o próprio HOME
efêmero — então o teste confunde o efêmero com o real e acusa um vazamento que não
existe. Como a suíte roda dentro do sandbox, **toda task da sprint falhava**, e a
mensagem não apontava para o isolamento como causa.

**Decisão:** o sandbox passa a se identificar com `--setenv AUTODEV_SANDBOX 1`, e
os 2 testes que só fazem sentido no host declaram `skipif`.

**Consequência:** "verde na minha máquina" não vale quando o teste roda em outro
ambiente. A linha de base precisa ser medida **onde o portão roda**, não onde é
cômodo rodar.

---

## D-03 **[bug]** — o portão de integração reprovava por falta de comando

**Contexto:** `integrar()` chamava `rodar_portoes()` sem informar comando de teste.

**Fato:** sem comando, o portão cai na auto-detecção — que devolve `"nenhum"`
quando o projeto não casa com os detectores (sem `pyproject.toml`, sem
`pytest.ini`, sem `tests/` na raiz). Este repositório é exatamente assim. O runner
saía com **127** e o portão reprovava **sempre**, por AUSÊNCIA DE COMANDO e não por
teste vermelho — e a mensagem não distingue os dois casos.

**Decisão:** a integração usa `aceitacao.comando` do `sprint.yaml`, com o campo
`teste` da primeira task como reserva, e avisa alto quando nenhum dos dois existe.

**Consequência:** cada task passava nos seus testes e a integração rejeitava as
dez. Portão que não sabe o que executar não é portão — é obstáculo.

---

## D-04 **[bug]** — o portão de segurança acusava a si mesmo

**Contexto:** o portão de segurança procura segredos por padrão, incluindo
`\bsk-[A-Za-z0-9]{12,}`.

**Fato:** o **teste** do portão escrevia um literal com forma de chave de API
(`'API_KEY = "sk-abcdef1234567890"'`) para provar que a detecção funciona. O
arquivo de teste passou a casar com o próprio padrão, e o portão reprovava o
repositório por causa do teste do portão. A integração inteira caiu.

**Decisão:** o valor sintético é **montado em runtime** (`"sk-" + "x" * 20`). O
arquivo escrito continua idêntico e o fonte deixa de casar.

**Consequência:** vale para qualquer scanner cujo fixture precise ter a forma
daquilo que ele procura. O teste de um detector é um ponto cego do detector.

---

## D-05 **[bug]** — o sandbox escrevia uma cópia do token DENTRO do worktree

**Contexto:** `preparar_home()` deriva o HOME efêmero de
`Path(__file__).resolve().parent.parent / ".autodev/sandbox-home"` e copia o
`~/.codex/auth.json` real para lá (permissão 600).

**Fato:** quando um teste da suíte exercita o sandbox **de dentro do sandbox**,
`__file__` aponta para o `sandbox.py` do **worktree em teste**. O HOME sintético —
com uma cópia do token OAuth — era escrito **dentro do worktree**. O portão de
segurança encontrava `.autodev/sandbox-home/.codex/auth.json` lá e reprovava a
integração de todas as tasks, com a mensagem "arquivo sensivel versionado", que não
aponta para o sandbox como causa.

**Decisão:** `preparar_home()` detecta `AUTODEV_SANDBOX=1` e **reaproveita** o HOME
efêmero que já existe, sem preparar outro.

**Consequência:** ferramenta que escreve artefato relativo a `__file__` contamina
o diretório onde está sendo testada. Isto também reduz (não elimina) a exposição
levantada no **HAQ-001** — o item continua aberto, porque mover o arquivo não muda
a política de copiar a credencial.

---

## D-06 **[bug]** — a detecção de processo do vigia se auto-acusava

**Contexto:** o vigia noturno detectava execução em andamento com
`pgrep -f "autodev --sprint DEVFACTORY-002 run"`.

**Fato:** o padrão casa com **qualquer** processo que mencione o texto — inclusive
o shell usado para testar o próprio vigia. Na primeira execução o vigia detectou a
si mesmo, concluiu que já havia execução ativa e **não lançou nada**. Teria
passado a noite inteira em silêncio.

**Decisão:** `proc_rodando.py` lê `/proc/<pid>/cmdline`, compara os **tokens de
argv** e exige que o executável seja o interpretador Python.

**Consequência:** verificação de vivacidade não pode casar com o observador.
Qualquer checagem por texto de linha de comando casa com quem a escreveu.

---

## D-07 **[aberto]** — o modelo de dependências do motor está errado

**Contexto:** o DAG declara dependências entre tasks e o orquestrador as respeita
para **ordem**: uma task só é selecionada quando suas deps estão `DONE`/`INTEGRATED`.

**Fato:** `WorktreeManager.criar()` usa `base = base or commit_atual(self.repo)` e
o orquestrador chama `wm.criar(sprint, task_id, agente)` **sem base**. Toda task
nasce da `main`. Uma task que declara `deps` espera pela dependência mas **começa
sem o código dela**: precisa reimplementar o que a dependência já fez, e escreve a
sua própria versão dos mesmos arquivos. A integração acontece **uma única vez, no
fim**, então cada branch traz a sua versão a partir da main e os merges colidem.

Resultado da primeira noite autônoma (2026-09-27): **1 de 10 tasks integradas, 9
bloqueadas por conflito de merge em cinco rodadas seguidas**. A cota nunca foi o
problema (`esperas_cota = 0` em todas as tasks) — o gargalo foi o modelo de
dependências.

O DAG é respeitado para ORDEM e nunca para CONTEÚDO.

**Prova executável:** `test_task_dependente_enxerga_o_trabalho_da_dependencia` em
`.autodev/tests/test_acceptance.py`, marcado `xfail(strict=True)`. Ele passa no dia
em que o modelo for corrigido — e a marca estrita transforma o `XPASS` em falha,
obrigando a remover o marcador.

**Por que o ensaio não pegou:** o ensaio com agente simulado rodou 10 tasks em 6
ondas até a integração com sucesso, porque o driver falso escrevia um arquivo
**distinto** por task — nunca havia disputa. Sobreposição de arquivo é exatamente o
que os dados reais têm e os simulados não tinham. Ensaio com dados falsos valida o
encadeamento, **não** o modelo de dados.

**Opções de correção (não decididas):**

| # | Abordagem | Efeito |
|---|---|---|
| A | Integrar **a cada task**, assim que ela fica `DONE`, e criar o worktree da task seguinte a partir do **tip do branch de integração** | Modelo correto e convencional. Muda o fluxo de `rodar()`: a integração deixa de ser um passo final e passa a ser parte do laço |
| B | Criar o worktree a partir do **merge das branches das deps** da task | Resolve o conteúdo sem mudar quando se integra, mas exige merge temporário e complica o caso de dependência transitiva |
| C | Detectar a sobreposição **antes** de executar e recusar o DAG | Não corrige: só impede o sprint de rodar. Útil como validação adicional, não como solução |

**Decisão:** **pendente** — é mudança de arquitetura do motor e requer revisão do
autor antes de ser implementada.

**Consequência:** enquanto D-07 estiver aberto, o motor serve para sprints cujas
tasks tocam arquivos disjuntos. Para backlogs reais, onde tasks se sobrepõem, o
resultado é o da primeira noite.

---

## Decisões herdadas (pré-sprint)

| Decisão | Regra |
|---|---|
| Merge em main | **nunca** automático; trabalho fica no branch do sprint |
| Sandbox | `bwrap`, sem daemon e sem root; HOME real nunca montado |
| Revisão | quem implementa não revisa |
| Cota | espera de recurso, não falha |
| Skills de terceiros | auditar antes de instalar |
| Saída de skill | sempre em `~/workspace/s_<skill>/` |
| Solução sem repositório | spec de problema/solução em `~/spec_solucoes_dev/` |
