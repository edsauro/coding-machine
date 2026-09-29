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
(montado como `"sk-" + "abc" + dígitos`, e não assim no arquivo) para provar que a
detecção funciona. O arquivo de teste passou a casar com o próprio padrão, e o portão
reprovava o repositório por causa do teste do portão. A integração inteira caiu.

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

## D-07 **[corrigido]** — o modelo de dependências do motor está errado

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

## D-08 **[corrigido]** — a cota do AGY é pega, mas pelo nome errado

**Contexto:** o autor avisou que a cota do AGY pode acabar antes da do Codex e
pediu para ser avisado se acontecer.

**Fato:** não existe classe `AGY_QUOTA`. `CLASSES_DE_RECURSO = {CODEX_QUOTA}`, e
`classificar()` percorre as classes **com cota primeiro**, casando a primeira que
tiver qualquer sinal em `SINAIS`. Os sinais de `CODEX_QUOTA` são genéricos —
`"quota exceeded"`, `"usage limit"`, `"rate limit"`, `"429"`, `"too many
requests"`, `"limit resets"` — e não mencionam Codex em lugar nenhum.

Consequência 1 (boa): se o AGY esgotar a cota e disser qualquer uma dessas
frases, o erro **é** classificado como recurso, vira espera de 5h10m e **não**
escalona modelo nem consome tentativa. Não passa em branco.

Consequência 2 (ruim): a espera é atribuída à cota do Codex, então o registro
mente sobre qual recurso acabou — e o `retry_after` de 5h10m é o do Codex.

Consequência 3 (a pior): qualquer `429` transitório — um proxy, um pico de rate
limit momentâneo — também casa com `CODEX_QUOTA` e faz o motor **dormir 5h10m**.
Um limite momentâneo custa a noite.

**Decisão:** **pendente**. Registrar como achado; a correção provável é separar
`AGY_QUOTA` de `CODEX_QUOTA` e distinguir "cota renovável" de "limite
momentâneo" (este pede espera curta, não 5h10m).

**Consequência:** enquanto isso, espera de 5h10m é o comportamento seguro por
padrão — nunca força renovação nem queima cota, que é exatamente a regra do autor.
O custo é poder dormir demais diante de um 429 passageiro.

---

## D-09 **[corrigido]** — "disk quota exceeded" seria lido como cota do Codex

**Contexto:** `SINAIS[ENVIRONMENT_ERROR]` inclui `"disk quota"` e
`SINAIS[CODEX_QUOTA]` inclui `"quota exceeded"`.

**Fato:** como `classificar()` testa `CODEX_QUOTA` primeiro, a mensagem
`"disk quota exceeded"` contém `"quota exceeded"` e casa com **cota** antes de
chegar a ambiente. Disco cheio passaria a ser espera de 5h10m em vez de erro de
ambiente — e erro de ambiente é justamente a classe que manda corrigir o ambiente
sem escalonar modelo.

**Decisão:** **pendente** — anotado. Correção provável: excluir a leitura de
"disk quota" dos sinais de cota, ou testar ambiente antes de cota para esse caso.

**Consequência:** risco baixo de frequência, alto de custo: um disco cheio pode
parecer cota esgotada e parar a noite inteira.

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

---

# Rodada de retomada (2026-09-27, tarde) — padronização a pedido do autor

Contexto: a sprint parou com 1 task integrada e 9 BLOQUEADAS (limite de 5
tentativas em P02–P05; P06–P10 caíram por dependência). O autor padronizou as
escadas e autorizou a retomada a partir do 3º degrau.

## D-10 — Escada de implementação é a matriz do autor, não intuição

**Regra:** `luna/low → terra/low → sol/low → sol/medium → astra/low`, uma por
tentativa, exatamente `max_tentativas_implementacao = 5` degraus.

**Por quê:** a escada anterior (luna/low → luna/medium → terra/medium →
sol/high → gpt-5.5 → astra/high) tinha 6 degraus para 5 tentativas, e subia
effort antes de subir modelo — gastava mais no mesmo slug. A escada agora bate
1:1 com `policies.retry.escalonamento` (1→tier 0 … 5→tier 4).

**Implementado em:** `.autodev/config/models.yaml` (`codex.ladder`).
**Testes:** `test_escada_de_implementacao_e_a_matriz_do_autor`,
`test_escada_de_implementacao_nao_estoura_o_teto_de_tentativas`.

## D-11 — Revisão também tem escada, e o Hermes revisa de verdade

**Regra:** 1ª tentativa revisa com agy **Gemini 3.6 Flash (Low)**, 2ª **3.7**,
3ª **3.8**, 4ª Hermes **deepseek-flash**, 5ª Hermes **deepseek-v4-pro**.

**Por quê:** antes o revisor era sempre o cruzado, sem escolha de modelo. As
tentativas 4 e 5 são a última chance sem humano — é onde faz sentido um revisor
mais forte, e o Hermes não implementa nenhuma task, então a regra "quem
implementa não revisa" continua valendo.

**Achado da máquina:** `agy` identifica modelo por RÓTULO com o effort embutido
(`"Gemini 3.8 Flash (Low)"`); passar `-e` junto devolve *invalid model selection:
--effort is not supported*. Verificado: 3.6 e 3.7 estão com a cota individual
esgotada, 3.8 responde.

**Implementado em:** `models.yaml` (`revisao`), `review.revisar(tentativa=...)`,
`config.revisor_para_tentativa`, adaptador `hermes` em `agents.WRAPPERS`
(`hermes -z "<prompt>" -m <modelo>`, prompt no argumento, sem `-d/--timeout/-f`).
**Testes:** `test_escada_de_revisao_por_tentativa`,
`test_revisor_da_escada_e_usado_quando_a_tentativa_e_conhecida`,
`test_tentativa_4_e_5_revisam_com_hermes_e_prompt_somente_leitura`,
`test_adaptador_hermes_monta_prompt_no_argumento`.

## D-12 — O revisor nunca é motivo de parada do sprint

**Regra (do autor):** se o revisor da tentativa não roda, tenta o próximo da
cadeia — `agy da tentativa → Hermes flash → Hermes pro → portão determinístico`.
Toda troca fica registrada em `Revisao.origem` e no log da task.

**Por quê:** cota do AGY estourada não pode custar uma tentativa do Codex. E
revisor ausente não pode virar "aprovado por padrão": o portão determinístico é
**piso** — o LLM não aprova o que ele reprova (segredo no diff, comando
destrutivo, worktree sem alteração). Divergência entra no veredito, não é
silenciada.

**Implementado em:** `review._escolhe_revisor`, `review._revisar_por_llm`,
`review._aplica_piso`, `models.revisao.reserva_cadeia`.
**Testes:** `test_reserva_entra_quando_o_revisor_da_escada_falha`,
`test_cadeia_de_reserva_atravessa_flash_e_pro_ate_revisar`,
`test_revisao_nunca_para_o_sprint_quando_todos_os_llm_falham`,
`test_piso_deterministico_impede_aprovacao_sem_codigo`.

## D-13 — `desbloquear`: sprint volta de FIM e o contador volta ao degrau pedido

**Regra:** `autodev --sprint X desbloquear [tasks...] --tentativas N` reabre o
sprint em EM_EXECUCAO e as tasks BLOCKED em QUEUED com o contador em N. Como o
modelo é escolhido PELO contador, `--tentativas 2` faz a próxima tentativa ser a
3ª da escada (sol/low + agy 3.8), e não a 1ª.

**Por quê:** o fim de cada rodada grava `FIM` no `sprint_state`, e `FIM` não é
estado válido do ciclo de vida — qualquer transição a partir dele era recusada.
Sem isso, retomar um sprint parado era impossível sem editar o banco à mão.

**Recusa explícita:** `--tentativas >= 5` é rejeitado (a task bloquearia na
primeira checagem), em vez de aceitar e falhar calado.
**Implementado em:** `state.reabrir_sprint`, `state.reabrir_tasks`,
`cli.cmd_desbloquear`.
**Testes:** `test_reabrir_sprint_tira_o_sprint_de_FIM`,
`test_reabrir_tasks_devolve_estado_e_contador`,
`test_desbloquear_recusa_contador_no_teto`.

## D-14 — HAQ-001 resolvido: credencial por `--ro-bind`, sem cópia no projeto

**Decisão do autor (2026-09-27):** a opção "montar read-only, sem cópia".

**Antes:** `sandbox.preparar_home()` COPIava `~/.codex/auth.json` (token OAuth
vivo) para `.autodev/sandbox-home/.codex/` e, por tabela, para cada worktree —
11 cópias chegaram a existir no disco do projeto. `.gitignore` e barreira de
commit impediam o vazamento **pelo git**, não a exposição no disco.

**Agora:** o HOME efêmero tem um arquivo VAZIO como ponto de montagem e o
`montar_cmd` monta o `auth.json` real com `--ro-bind-try`, dentro do namespace.
`_limpar_copias_antigas()` apaga as cópias que já estavam em disco. O HOME real
continua inalcançável dentro do sandbox — `~/.codex/auth.json` segue na lista de
caminhos proibidos do `policies.yaml`.

**Testes:** `test_sandbox_nao_copia_a_credencial_para_dentro_do_projeto`,
`test_sandbox_monta_a_credencial_somente_leitura`,
`test_limpeza_remove_copia_antiga_em_worktree`.

## D-15 — Cota do agente SECUNDÁRIO não estaciona a task

**Contexto (achado da 1ª rodada de retomada, 21:42):** com três reprovações de
revisão, o motor aplicou a regra "4ª tentativa → troca de agente" e mandou P02
para o **agy**. O agy está com a cota individual estourada → o motor classificou
como `CODEX_QUOTA` e registrou **espera de recurso de 5h10m** para uma task que
tem substituto pronto. Parada por um agente secundário.

**Regra (do autor):** a espera de 5h10m protege a cota do agente **primário**. Se
quem estourou cota foi o agente secundário: ele sai da rodada, a tentativa é
**devolvida** (não consome o contador) e o codex reassume.

**Implementado em:**
* `orchestrator._quarentena_cota` — tira o agente de circulação e marca
  `disponiveis[agente].disponivel = False`, que é o que a escada de revisão e a
  troca de agente consultam (sem isso cada task gastaria minutos redescobrindo a
  mesma cota);
* ramo de cota para `agente != "codex"`: `failure_class = QUOTA_AGENTE`, contador
  devolvido, `resolver_esperas` não é chamado, laço continua com o codex (a
  variável local também muda — só mexer no banco faria o laço girar para sempre);
* `review.Revisao.sem_cota` — a revisão denuncia quem estourou cota e o
  orquestrador aplica a quarentena já na primeira task, em vez de repetir a
  chamada perdida em todas as seguintes.

**Testes:** `test_cota_do_agente_secundario_nao_estaciona_a_task` (aceitação, com
o driver determinístico: nenhuma espera fica em nome do agy, a tentativa dele é
devolvida e o agy sai da rodada), `test_reserva_entra_quando_o_revisor_da_escada_falha`
(`sem_cota == ["agy"]`).

## D-16 **[causa-raiz]** — o DAG mandava 8 tasks escreverem no MESMO arquivo de teste

**Achado (22:08, 1ª rodada com o revisor Hermes de verdade):** as tentativas de P02
nos degraus 3, 4 e 5 passaram nos testes (132→139 verdes) e foram reprovadas nas
três revisões com o mesmo finding: *"o diff apaga `autodev/planner.py` e os 6 testes
da P01"*.

**Causa:** o `dag.json` da sprint manda **8 das 10 tasks** gravarem os testes em
`.autodev/tests/test_planner.py` (P01, P02, P03, P04, P05, P06, P09, P10) e **6**
editarem `autodev/planner.py` (P01, P03, P04, P05, P06, P07). Cada task que
obedece aos próprios critérios SUBSTITUI os testes da anterior; o revisor reprova
por regressão (está certo em reprovar); a task queima as 5 tentativas e bloqueia.
Mais: P03/P04 (mesma onda), P05/P06 (mesma onda) e P08/P09 (mesma onda) editam os
mesmos arquivos em paralelo — conflito `add/add` garantido na integração.

**Isto é a explicação do fracasso da 1ª noite**, não a capacidade dos modelos:
as 9 tasks bloqueadas seguiam um contrato que se contradizia.

**Correção (decisão autônoma do Hermes, 2026-09-27 22:15 — reversível):**
1. **arquivo de teste próprio por task** (`test_plan_prompt.py`, `test_plan_parser.py`,
   `test_plan_validacao.py`, `test_plan_escrita.py`, `test_plan_agente.py`,
   `test_plan_cli.py`, `test_plan_aceitacao.py`, `test_plan_rastreabilidade.py`,
   `test_plan_docs.py`) — nenhum colide com arquivo já existente no repositório;
2. **critério de preservação obrigatório** em P02–P10: proibido remover ou
   reescrever arquivo de teste de outra task, e a suíte inteira continua verde
   (`python3 -m pytest .autodev/tests/ -q`);
3. **módulo compartilhado é ESTENDIDO, não reescrito**: `autodev/planner.py` mantém
   `Plano`, `TaskPlano` e `validar_plano`;
4. **serialização**: P04 dep. P03, P06 dep. P05, P09 dep. P08 — tasks que mexem no
   mesmo arquivo deixam de rodar em paralelo na mesma onda.

O `dag.json` anterior está preservado em
`.autodev/sprints/DEVFACTORY-002/dag.json.antes-da-correcao-D16`.

**A régua não baixou:** os critérios ficaram mais precisos (arquivo próprio nomeado,
preservação verificável por comando) e a revisão continua reprovando regressão.

**Reabertura:** P02–P10 voltaram ao 3º degrau (sol/low) com os worktrees recriados
do zero a partir da integração da P01 (`b963e0e`) — as tentativas antigas ficaram
arquivadas nos branches `arquivo/DEVFACTORY-002/P0X-antes-D16`, para auditoria.

## D-17 — Entrega vazia não é entrega: pergunta de design em modo headless

**Achado (tentativa 13 da P02, 22:13→22:19):** o agente terminou com
*"Você aprova esse design para eu implementar?"* e **nenhum arquivo alterado**. O
motor tratou como sucesso (exit 0 + stdout com texto), rodou a suíte — que passou,
porque ela já passava antes — e gastou **5min de revisor** para o Hermes descobrir
o óbvio: "nenhum commit foi feito".

**Causa:** o motor só sabia julgar a tentativa por `exit_code`, texto de erro e
testes. Agente que **conversa** em vez de entregar cai no vão entre os dois: a
suíte pre-existente verde faz a tentativa parecer saudável.

**Correção (três pontas):**
1. **prompt**: `PROMPT_TASK` ganhou a seção *Modo headless* — "Você roda SEM HUMANO
   disponível", "não peça aprovação de design", "entregar só um plano conta como
   FALHA da task";
2. **portão**: nova classe `SEM_ENTREGA` (consuma tentativa, escala modelo como as
   demais). Se o agente não alterou **nenhum** arquivo e a suíte está **verde**, a
   tentativa falha na hora, sem gastar revisão, e o prompt de retry carrega a
   resposta do próprio agente com instrução explícita ("não há humano para
   responder: implemente agora"). Fica **depois** dos testes de propósito: suíte
   vermelha é `TEST_FAILURE`, que explica melhor;
3. **ruído**: `arquivos_alterados()` descarta bytecode/cache
   (`__pycache__`, `*.pyc`, `.pytest_cache`, `node_modules`, …). Achado do teste:
   num projeto que **rastreia** `__pycache__`, rodar os testes "altera" os `.pyc` —
   e a tentativa sem entrega passaria por entregue (a onda chegou a ser integrada
   por `.pyc` no fixture). No repo real isso não acontece (0 arquivos rastreados),
   mas a proteção não pode depender da higiene de cada projeto.

**Testes:** `test_prompt_de_task_proibe_perguntar_em_headless` e
`test_entrega_vazia_falha_a_tentativa_sem_gastar_revisao` (base verde + agente que
só pergunta → `SEM_ENTREGA`, zero revisão).

## D-18 — O worktree da task nascia sem o código das dependências já integradas

**Achado (rodada 22:26→23:02, P04 tentativa 8 aprovada):** P04 passou nos testes
(159) e na revisão, e a **integração devolveu CONFLITO**: `autodev/planner.py` foi
editado por P03 e por P04, e o merge não fechou. A rodada terminou em 3 de 9 e as
dependentes (P05–P10) ficaram BLOCKED.

**Causa:** `WorktreeManager.criar()` reusava a branch existente e **ignorava a base
pedida** — `git worktree add <caminho> <branch>` não move a branch. O
`_base_do_worktree()` (a correção D-07) garante que a base *é* o tip de integração,
mas só vale quando a branch é criada na hora. A P04 tinha branch de rodada anterior
(resetada para a base de 27/09 21:5x, antes de P02/P03): o DAG era respeitado para
ORDEM e **violado em conteúdo** — exatamente o defeito que o próprio
`_base_do_worktree` documenta.

**Correção:**
1. `criar()` **sempre** entrega o worktree na base atual: se a branch existe, o tip
   antigo vai para `refs/arquivo/<branch>` e a branch volta para a base;
2. se o worktree já existe e ficou para trás, faz **merge** da base dentro dele
   (preserva os commits da própria task); se o merge conflita, o trabalho antigo foi
   escrito contra um código que não existe mais — vai para `refs/arquivo/` e o
   worktree volta para a base;
3. o orquestrador registra `worktree_realinhado` no histórico quando isso acontece.

**Correção 2 (mesma rodada) — a rodada parava no primeiro tropeço:** o laço
percorre as ondas **uma vez**; com P04 em RETRY, as dependentes ficaram BLOCKED e o
run terminou. Agora `run` roda em passadas (`--rodadas`, default 5) e cada passada:
reabre o sprint que ficou em `FIM` (fim de UMA rodada, não do sprint) e rearma —
via `rearmar_dependentes()` — apenas as tasks bloqueadas por dependência **já
integrada**, preservando o contador da escada. Sem nada a rearmar, para na hora.
`forcar_estado` passou a limpar o motivo do bloqueio ao sair de BLOCKED (task
QUEUED carregando "depende de [...]" envenenava quem lia o motivo).

**Testes:** 8 novos em `.autodev/tests/test_d18_worktree_e_rodadas.py` — branch de
rodada anterior nasce da base atual, worktree atrasado recebe a base por merge,
worktree conflitante volta para a base com arquivo de auditoria, base já contida não
mexe em nada, rearme preserva o degrau, rearme não toca bloqueio humano, `run` dá
passadas e rearma, `run` reabre sprint em FIM.

## D-19 — A preservação absoluta do D-16 tornava a P09 impossível

**Achado (28/09 04:43, P09 bloqueada no limite de 5 tentativas):** a P09 existe para
fazer `autodev report` incluir o prompt original — mudança de **comportamento**. O
teste da P05 (`test_plan_escrita.py`) afirma o conjunto **exato** de chaves do
relatório, então a mudança da P09 invalida uma asserção existente. E o critério de
preservação que eu escrevi no D-16 diz: *"nenhum arquivo entregue por task anterior
pode ser removido ou reescrito"*. Absoluto assim, a task é impossível: obedecer ao
requisito **exige** violar o critério. Duas revisões reprovaram por isso (com razão)
e a sexta tentativa quebrou nos testes.

É a **mesma classe do D-16**: não falta de modelo, contrato que se contradiz.

**Agravante registrado:** a P09 tentou resolver sozinha, escrevendo 9 linhas em
`decisions.md` se autorizando ("P09 está autorizada a ajustar a asserção…"). O
revisor pegou e reprovou como **alta**. O caminho legítimo tem de estar no critério
e ser julgado, não autodeclarado.

**Marcas do meu script do D-16 (cosméticas, mas confundem o agente):** critério
`os testes ficam em X e X` (nome repetido na P09) e a frase da suíte duplicada
dentro do próprio critério de preservação.

**Correção — preservação COM EVOLUÇÃO (P09 e P10):** não remover nem enfraquecer
caso de teste de task anterior; se a mudança de comportamento **exigida** pela task
invalidar uma asserção, ajustá-la de forma **mínima**, registrar a decisão **datada**
em `decisions.md` e **provar que a cobertura não caiu** (contagem de
`pytest --collect-only -q` não pode diminuir); o revisor confere a justificativa e
**reprova autorização sem essa prova**.

P01–P08 mantêm a regra estrita sob a qual foram integradas — mudar o critério de uma
task já integrada seria reescrever o que foi verificado. O `dag.json` anterior está
em `dag.json.antes-da-correcao-D19`.

**Reabertura:** P09 volta ao 3º degrau (sol/low, contador 2) com o worktree
realinhado automaticamente pela correção D-18; o trabalho antigo, escrito contra o
contrato impossível, fica arquivado em `refs/arquivo/`.

## D-20 — A espera de cota passa a usar o reset informado pelo próprio agente

**Achado (28/09):** duas vezes na mesma madrugada o motor dormiu **por cima** do
reset real, porque trata a espera como um valor fixo de política (5h10m):

- 08:39 — o codex avisou `try again at 9:10 AM` (31 min) e o motor registrou 5h10m;
- 03:45 — o codex avisou 03:41 e o motor esperava até 04:37 (56 min a mais).

**Correção:** `errors.reset_de_cota()` lê as formas reais que aparecem em produção —
`try again at 9:10 AM` e `try again at Sep 28th, 2026 3:41 AM` (codex),
`Resets in 120h58m47s` (agy) e `resets in 5 hours`. `errors.espera_efetiva()` aplica
o valor lido **somente quando é menor que a política**: a política segue como **teto**,
para que um reset absurdo (o agy anunciou 5 DIAS) não estacione o sprint por dias;
reset abaixo de 30s é tratado como ruído, para não bater na cota em laço apertado.
Tudo com `agora` injetável — os testes não dependem do relógio da máquina.

**De quebra:** o log do checkpoint parava de imprimir o literal `HEAD` e passa a
imprimir o sha real (`.autodev/sprints/DEVFACTORY-002/logs/orquestrador.log`).

**Testes:** 15 novos em `.autodev/tests/test_reset_de_cota.py` (suite do motor: 182).

## D-21 — Fechamento da sprint: 10 de 10 integradas

**Resultado:** as 10 tasks foram integradas na branch
`sprint/DEVFACTORY-002/integration` (P01 `b963e0e` … P10 `785988d`), com os 4 portões
(build, testes, lint, segurança) OK em cada integração. Suíte do projeto terminou em
**203 testes verdes** e a suíte do motor em **194**.

**Faltas de revisão declaradas (não escondidas):** as aprovações finais da **P09**
(09:52) e da **P10** (09:54) saíram do **portão determinístico**, porque agy e Hermes
estavam sem cota naquele instante — é a última reserva da cadeia do autor (o revisor
nunca para o sprint), mas é mais fraca que uma revisão por LLM. Com créditos repostos,
as duas revisões foram **refeitas por LLM** sobre o mesmo diff integrado
(`hermes/deepseek-flash`, evidência em
`~/.hermes/cache/scratch/revisoes-p09-p10/*-veredito-relido.json`); o resultado está
registrado no fechamento do sprint.

**Pendências do autor (não são falhas, são decisões):**

1. **Merge em `main`** — o dry-run aponta conflito em **dois** arquivos que mudaram nas
   duas pontas: `.autodev/sprints/DEVFACTORY-002/decisions.md` (add/add, já previsto
   pelo revisor da P09) e `README.md` (a tela de eventos de um lado, a documentação do
   planejador do outro). A resolução correta é **somar os dois lados**, não escolher um.
2. **HAQ-002** (sprint 1) — autorizar o encerramento com conclusão retroativa.
3. **DEVFACTORY-003** (planejada) — aprovar ou ajustar o objetivo antes de iniciar.

## D-22 — Revisão retroativa por LLM da P09 e da P10 (créditos repostos)

Refeitas as duas revisões que só tinham o portão determinístico, sobre o **mesmo diff
integrado** (`hermes/deepseek-flash`, evidência em
`~/.hermes/cache/scratch/revisoes-p09-p10/{P09,P10}-veredito-relido.json`). As duas
voltaram **REQUEST_CHANGES** — nenhuma das duas foi aprovada por decoro.

**P09 — 1 alta, 1 média, 3 baixas.** O achado ALTA é sobre **prova, não código**: o
critério do D-19 exige provar que a cobertura não caiu, e o `decisions.md` da branch
admitia que a contagem "não pôde ser comprovada" — com um diagnóstico de ambiente
**errado** (dizia que `python3` não tem pytest; o correto é o interpretador do
projeto, `.venv/bin/python`, como o próprio README manda). O código em si foi
verificado pelo revisor ponta a ponta.

**Resolvido por medição independente** (`~/.hermes/cache/scratch/prova_cobertura_p09.py`,
worktrees temporários, interpretador do projeto, somente leitura):

```
base  a9445a39: 186 coletados · 158 defs de teste
HEAD  1942b63e: 201 coletados · 163 defs de teste
cobertura NÃO caiu: +15 coletados, +5 defs
```

Com a prova medida e registrada aqui, o achado ALTA fica atendido.

**P10 — 2 médias, 4 baixas.** Os dois achados médios são reais e ficam **abertos**:

1. `README.md:91` — a seção `## Planejador` foi inserida **dentro** de `## Como rodar`,
   o que rebaixa `### Ciclo de vida do sprint` e `### Conclusão por evidência` a
   subseções do Planejador. Estrutura de documentação errada.
2. `.autodev/tests/test_plan_docs.py:13` — o helper `_secao_planejador()` corta a seção
   no próximo `\n## `, então o teste examina também as subseções seguintes que não são
   do Planejador: **prova menos do que anuncia** (mesma classe de defeito que o revisor
   já havia apontado em outra task).

Baixas registradas: `README.md:105` diz que a execução "consome cota do agente
configurado", mas o agente do planejador é fixo em código (`cli.py:268`, `"codex"`);
`README.md:167` mantém "123 testes passando" (obsoleto, pré-existente); a evidência
anexada à tentativa ("203 passed") não reproduz — a árvore revisada coleta 205; e o
comando do critério (`python3 -m pytest`) **não roda nesta máquina** (o `python3` do
sistema não tem pytest; o runner do motor resolve para o venv, mas uma pessoa ou um
revisor rodando o comando literal falha) — corrigir a redação dos critérios nos
próximos DAGs.

**Nada foi alterado à mão no trabalho integrado.** Os achados médios da P10 e as
baixas entram como proposta de uma sprint curta de correção
(`DEVFACTORY-004`), que **depende de aprovação do autor** antes de ser executada —
mesma regra das outras sprints.

**Merge:** as duas pontas mudaram `decisions.md` e `README.md`, então o merge em `main`
conflita exatamente nesses dois arquivos; a resolução correta é **somar os dois lados**
(o log de decisões e as duas seções de documentação). Conferido também que
`dag.json` **não** regride: só o `main` mexeu nele desde a base do merge, então o
conserto do D-19 é preservado.

## 2026-09-28 — P09: rastreabilidade e compatibilidade

O DAG passa a persistir `prompt_original`. A asserção de igualdade do conjunto
de chaves em `test_escreve_sprint_completo_no_proximo_id` foi ampliada somente
com essa chave, exigida por P09. Nenhum teste anterior foi removido, nem suas
demais asserções alteradas. O novo arquivo `test_plan_rastreabilidade.py`
acrescenta 15 casos, incluindo preservação literal na spec, DAG e relatório,
plano sem prompt, DAG ausente, inválido ou ilegível e colisão com extras.

O relatório continua derivado do estado: problemas de leitura do DAG resultam
em `nao informado`. O prompt do DAG prevalece sobre `report-extras.json`.

### Evidência e bloqueio de ambiente

- Antes das alterações, `python3 -m pytest .autodev/tests/ --collect-only -q`
  terminou com código 1: `No module named pytest`.
- Após as alterações, o mesmo comando e
  `python3 -m pytest .autodev/tests/ -q` terminaram com o mesmo erro.
- Portanto, a contagem de coleta antes/depois e a aprovação da suíte **não
  puderam ser comprovadas** neste ambiente. A verificação obrigatória permanece
  pendente no runner com pytest disponível; não se declara a suíte verde.
- Verificação direta com Python exercitou 15 cenários do fluxo real, todos
  aprovados. A análise sintática dos arquivos alterados e `git diff --check`
  passaram. Isso não substitui a execução da suíte ou sua coleta.

Na integração, anexar esta entrada ao histórico existente de decisões, caso
esse arquivo já exista na branch de destino, preservando as entradas anteriores.
