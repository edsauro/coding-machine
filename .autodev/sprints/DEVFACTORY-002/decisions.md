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
