# Pendências do Coding_Machine

Atualizado em **28/09/2026, ~23h** (Hermes) — depois de a `DEVFACTORY-002` e a
`DEVFACTORY-004` serem **mergeadas e publicadas** em `main`, de a `DEVFACTORY-003` entrar
em execução e de as decisões humanas de hoje (P-01, P-03, P-04, P-07, P-08) serem
aplicadas.
Estado dos sprints: **001 ENCERRADA (ratificada hoje)** · **002 ENCERRADA (10/10, em
`main`)** · **003 EM EXECUÇÃO** · **004 ENCERRADA (4/4, em `main`)**.

## Como este arquivo é mantido

Toda pendência entra aqui com o que é preciso para **resolvê-la**, não só com o
assunto:

1. **quem decide** (autor ou agente) e o que acontece se ficar parada;
2. **caminhos exatos** dos arquivos que precisam ser lidos/analisados;
3. **comando de verificação** que mostra o estado atual com os próprios olhos;
4. **critério de pronto** (como saber que acabou);
5. **evidência** onde o resultado fica registrado.

Quando uma pendência é resolvida, ela sai de "Abertas" e vira uma linha em
"Resolvidas" — com data e o que foi feito. O estado **de fato** está sempre no
`state.db`; este arquivo é o mapa do que está pendente e onde olhar.

---

## Abertas

### P-11 · Estudo A/B de modelos (espelho) nos pacotes limpos — **autor (aprova o gasto)**
- **O que é:** rodar o mesmo pacote, do mesmo commit-base, com o mesmo revisor, em N≥3
  repetições por braço (degraus da escada atual × 1–2 APIs externas de código),
  medindo aceite, chamadas, tokens, custo e tempo.
- **Plano completo:** `TesteAB-eficiencia-LLM.md` (raiz do projeto) — agora com a
  **seção 0**, o padrão de escolha dos braços. Candidatos: **P08 (002, 4 chamadas,
  4/0/0)** e **P05/P07 (002, 6 chamadas cada)**. Evitar P02/P03/P09 (002) e P01/P04
  (004) — dentro de janela de culpa de teste/plano.
- **Padrão de escolha (28/09, autor):** **Codex sempre no `low`** em qualquer modelo,
  **DeepSeek sempre no `high`** (flash e pro) → 6 braços.
- **Regras de leitura (28/09, autor):** tabela **ordenada por preço**; `Δ5h`/`Δsemanal`
  com **2 casas decimais**.
- **PRÉVIA DE CUSTO JÁ MEDIDA (28/09)** — 12 braços, 1 chamada cada, pacote mínimo, com o
  revisor fixo mais barato (deepseek-flash):
  **`~/workspace/s_llm-coding-efficiency/previa-custo/RELATORIO.md`**
  (`resultados.jsonl` cru; `medir.py` reproduz; `custos.py` gera a tabela **ordenada por
  preço** com o Δ **derivado dos tokens medidos**).
  - Codex pela assinatura do autor (US$ 20/mês = 4 blocos semanais de 100%): **US$ 0,05
    por 1% da janela semanal**, janela de 5h ≈ 1/7 da semanal → **~US$ 0,40 por milhão de
    tokens**; 1% da 5h ≈ 16.871 tokens. **A janela semanal é a que limita** (a semana tem
    33,6 janelas de 5h, mas a franquia vale ~7 delas).
  - Dentro do padrão, do mais barato ao mais caro: **deepseek-flash/high US$ 0,0027** ·
    codex **sol/low 0,0066** · luna/low **0,0105** · terra/low **0,0106** ·
    deepseek-v4-pro/high **0,0133** · astra/low **0,0201**.
  - Tokens ≠ custo: o deepseek gasta ~4× mais tokens por chamada e custa ~13× menos por
    token (~US$ 0,03/Mtok); piso de ~9,4 mil tokens por chamada de Codex.
  - `sol/high` **reprovou pedindo aprovação** ("Aprova esse desenho?") com `rc=0`: em
    headless, quem pergunta não entrega.
- **Custos das APIs externas:** o Hermes grava `--usage-file` com `estimated_cost_usd`
  (fonte oficial) por chamada; o **saldo** vem direto da API — DeepSeek
  `GET /user/balance` (**US$ 8,36** em 28/09, chave em `~/.hermes/.env`, nunca em tela).
  Não existe API de fatura por chamada.
- **Pronto quando:** tabela por braço com aceite/custo/tempo e um veredito que nomeie o
  perdedor e a condição que inverte a decisão.
- **Método:** skill `llm-coding-efficiency` (+ `references/custo-e-janela-codex.md`,
  `references/coding-machine-baseline.md`) e `capability-ab-test` (protocolo).
- **Cuidado de cota:** a franquia **semanal** do Codex está em **85%** (reset 04/10
  00:22) — o A/B com N≥3 consome muitas chamadas; começar depois do reset.

### P-14 · A espera de cota não sabe QUAL janela esgotou — **agente**
- **O que é:** quando o Codex recusa por cota, o motor grava a espera com prazo **fixo** de
  5h10m (`18600s`) e volta a tentar. Em 29/09 01:50 a janela de **5h** estava em ~30% e quem
  havia estourado era a **semanal** (97%, renovação 04/10 00:22) — a espera apontou para
  **07:00**, ou seja: vai acordar, falhar e re-esperar 5h10m, ~19 vezes até domingo. Não gasta
  token (a recusa é do CLI, antes do modelo), mas acorda o vigia a cada ciclo (um aviso no
  Telegram por ciclo) e escreve uma previsão de retomada errada no log e no `status`.
- **Medido:** `sqlite3 .autodev/state.db "SELECT datetime(detectado_em,'unixepoch','localtime'), datetime(retry_after,'unixepoch','localtime') FROM resource_waits WHERE sprint_id='DEVFACTORY-003'"` → 29/09 01:50 → **29/09 07:00**; a leitura da cota (`ler_cota.py`) dá 5h 30% / semanal 97%. O Codex publica
  as duas janelas (`primary` = 5h, `secondary` = 7 dias) com `usedPercent` e `resetsAt`.
- **Caminhos:** `autodev/state.py` (gravação/leitura de `resource_waits`), onde a cota é lida
  (`autodev/cli.py quota` — mesma fonte que o `quota` usa), `.autodev/config/policies.yaml`
  (o prazo fixo de 5h10m).
- **Pronto quando:** a espera mirar a janela que **realmente** esgotou — ler qual das duas
  está no limite e usar o `resetsAt` dela, com margem; cair no prazo fixo só se a leitura
  falhar. Teste: semanal esgotada + 5h folgada ⇒ `retry_after` = reset da semanal.
- **Hoje:** a 003 parou exatamente nesse estado (P06 em `WAITING_RESOURCE`, retry 07:00).
  Não implementei: mudar o motor fora do plano revisado não é o combinado — este texto é a
  proposta.

_P-12 e P-13 encerradas em 29/09 — estão no histórico de resolvidas acima._

---

## Relatórios e produtos por sprint

Caminhos relativos à raiz do projeto (`~/Code/Coding_Machine`), salvo indicado.

### DEVFACTORY-001 — laço autônomo (ENCERRADA, ratificada em 28/09)
| produto | caminho |
| --- | --- |
| spec / plano | `.autodev/sprints/DEVFACTORY-001/{spec.md,plan.md}` |
| plano executável | `.autodev/sprints/DEVFACTORY-001/dag.json` |
| decisões | `.autodev/sprints/DEVFACTORY-001/decisions.md` (D-13 = conclusão retroativa) |
| HAQ | `.autodev/sprints/DEVFACTORY-001/HAQ.md` (HAQ-001 e **HAQ-002 fechadas**) |
| **relatório final** | `.autodev/sprints/DEVFACTORY-001/SPRINT-REPORT.md` |
| logs | `.autodev/sprints/DEVFACTORY-001/logs/` |

### DEVFACTORY-002 — planejador: do prompt ao plano executável (ENCERRADA, 10/10, EM `main`)
| produto | caminho |
| --- | --- |
| spec | `.autodev/sprints/DEVFACTORY-002/spec.md` |
| plano executável | `.autodev/sprints/DEVFACTORY-002/dag.json` |
| planos anteriores (evidência) | `…/dag.json.antes-da-correcao-D16` e `…-D19` |
| decisões | `.autodev/sprints/DEVFACTORY-002/decisions.md` (D-01 … D-22 + a **ratificação da revisão retroativa**, 28/09) |
| **relatório final** | `.autodev/sprints/DEVFACTORY-002/SPRINT-REPORT.md` |
| evidência de teste (76) | `.autodev/sprints/DEVFACTORY-002/evidence/` |
| logs do motor | `.autodev/sprints/DEVFACTORY-002/logs/orquestrador.log` (+ por tentativa e revisão) |
| **revisão retroativa por LLM** | `.autodev/sprints/DEVFACTORY-002/revisao-retroativa/` |
| branch de integração | `sprint/DEVFACTORY-002/integration` (P01 `b963e0e` … P10 `785988d`) |
| branches por task | `sprint/DEVFACTORY-002/P0X-*`; tentativas antigas em `refs/arquivo/DEVFACTORY-002/` |
| logs das rodadas (fora do repo) | `~/workspace/a_devfactory/sprint-002-retomada/` |

### DEVFACTORY-003 — portão de qualidade do plano (EM EXECUÇÃO desde 28/09 22:58)
| produto | caminho |
| --- | --- |
| spec / plano / sprint | `.autodev/sprints/DEVFACTORY-003/{spec.md,dag.json,sprint.yaml}` |
| ondas | `[P01] → [P02,P03] → [P04] → [P05] → [P06] → [P07]` (7 tasks) |
| logs do motor | `.autodev/sprints/DEVFACTORY-003/logs/orquestrador.log` |
| vigia (cron) | `c9ff58c7925d` — `every 30m`, com **monitor** que só acorda o agente quando o estado muda |

### DEVFACTORY-004 — correções da revisão retroativa (ENCERRADA, 4/4, EM `main`)
| produto | caminho |
| --- | --- |
| spec / plano / sprint | `.autodev/sprints/DEVFACTORY-004/{spec.md,dag.json,sprint.yaml}` |
| decisões | `.autodev/sprints/DEVFACTORY-004/decisions.md` (D-23, D-24, D-25 + as das tasks) |
| **relatório final** | `.autodev/sprints/DEVFACTORY-004/SPRINT-REPORT.md` |
| evidência de teste (27 arquivos) | `.autodev/sprints/DEVFACTORY-004/evidence/` |
| logs do motor | `.autodev/sprints/DEVFACTORY-004/logs/orquestrador.log` (+ por tentativa e revisão) |
| branch de integração | `sprint/DEVFACTORY-004/integration` (P01/P04 → P02 → P03 `4ce5174`) |
| branches por task | `sprint/DEVFACTORY-004/P0X-*` |

### Ferramentas do repositório (não são de um sprint)
| produto | caminho |
| --- | --- |
| **tela de eventos** (permanente) | `./tela.sh` → `.autodev/scripts/tela.py` |
| verificador de planos | `.autodev/scripts/verificar_plano.py` |
| guarda de sobreposição (vigias) | `.autodev/scripts/rodada_em_andamento.py` |
| estado do sprint para o vigia (gate) | `.autodev/scripts/estado_003.sh` (+ wrapper em `~/.hermes/scripts/`) |
| backfill de tokens (histórico) | `.autodev/scripts/backfill_tokens.py [--gravar]` |
| **relatório de tentativas** (visual, regenerável) | `REPORT-TENTATIVAS-CODEX.md` + `report/` (PDF A4, 7 págs: Tabela 1 nas págs. 3–4 com **retrabalho bruto/ajustado em múltiplo**) |
| **prévia de custo do A/B** (12 braços) | `~/workspace/s_llm-coding-efficiency/previa-custo/RELATORIO.md` |
| testes do motor | `.venv/bin/python -m pytest .autodev/tests/ -q` (**325 verdes** depois dos merges) |
| estado (fonte da verdade) | `.autodev/state.db` |

---

## Resolvidas (histórico curto)

- **29/09** — **P-13 resolvida: a escada do motor escala só por MODELO.** Decisão do autor:
  o padrão de esforço vale também para o motor — todo degrau do Codex em **`low`**. O 4º
  degrau era `sol/medium` (escalonamento por **esforço**, que o padrão não permite) e passou
  a `astra/low`; como são 4 modelos para 5 degraus, o topo ocupa o 4º e o 5º
  (`luna → terra → sol → astra → astra`, tudo em `low`). **Efeito de custo, para o autor
  saber:** a 4ª tentativa fica ~50% mais cara que o antigo `sol/medium` (US$ 0,0201 contra
  US$ 0,0133 por chamada, na régua medida) — em troca, ela chega com o modelo do topo em vez
  de repetir o `sol` com mais esforço. Revisor segue `deepseek-flash/high` (P-13 é só a
  escada de implementação). Testes da matriz atualizados em dois arquivos.
- **29/09** — **P-12 resolvida: o contador `escalonamentos` conta de verdade.** O resumo lia
  `json_extract(test_result,'$.tier')` e **ninguém escrevia `tier` ali**: todo sprint
  reportava "0 escalonamentos" — número que se lê como fato. A fiação ficou do lado de quem
  sabe: o orquestrador compara o modelo/esforço escolhido com o da chamada **anterior da
  mesma task** e grava um evento `escalonamento` (com `de`, `para`, `tier_de`, `tier_para`);
  o resumo conta esses eventos. Subir conta; **voltar ao degrau barato** (rearme por
  dependência integrada, reabertura por defeito de contrato) **não conta** — senão o número
  mediria movimento, não subida. Quem decide é `Config.escalonou()`, com teste próprio.
  **327 verdes.**

- **29/09** — **Relatório restrito às sprints 003 e 004, com a razão declarada.** Os três
  gráficos e todas as tabelas passaram a considerar só as duas sprints do protocolo atual
  (uma chamada de API por tentativa, revisão registrada, token lido no rodapé): a 001 é
  história reconstruída (14 dos 15 pacotes sem chamada de API) e a 002 foi levantada depois
  do fato, com várias aprovações por pacote (até 6) e sem token medido — misturar as quatro
  media **protocolo de registro**, não modelo. O filtro é na **fonte** (`SPRINTS`, no
  gerador), então texto e gráficos não podem divergir; a razão aparece nos avisos e na
  legenda dos três gráficos, e cada gráfico diz o escopo na própria imagem. A **tabela de
  objetivos continua com as 4 sprints** (é mapa do plano, não medição) e ganhou CSS próprio:
  colunas sprint/pacote em 3,2/3,8 em, objetivo com o resto da largura, e quebra de página
  liberada (com o `page-break-inside: avoid` global ela pulava inteira e deixava uma página
  quase vazia). PDF em **8 páginas**.
- **28/09** — **Revisor passa a `deepseek-flash`/`high` — e o esforço do revisor volta a
  existir.** Pedido do autor, com duas correções: (a) a matriz de revisão
  (`.autodev/config/models.yaml`) tinha `deepseek-v4-pro` no 5º degrau **e** na cadeia de
  reserva (vem da matriz de 27/09, `3b94dc2`, e nunca havia sido mexida — conferido por
  `git log -S`); passou a `deepseek-flash` com `effort: high` nos dois degraus do Hermes, e
  a cadeia ficou com **um** degrau (dois degraus do MESMO modelo não traziam revisor
  diferente). (b) Defeito real encontrado no caminho: o esforço declarado do revisor
  **nunca chegava ao processo** — `autodev/review.py` passava `effort=None` fixo e o
  adaptador do Hermes (`autodev/agents.py`) ignorava esforço em silêncio (não existe `-e`
  no CLI do Hermes; existe `--reasoning`). Agora o esforço sai da matriz, é repassado à
  invocação e vai por `--reasoning`. Testes da escada de revisão atualizados + asserção
  nova provando que o `high` chega à invocação: **325 verdes**.
- **28/09** — **Relatório de tentativas ganhou o Gráfico 3 (custo estimado por pacote) e a
  tabela de objetivos de todos os pacotes.** O Gráfico 3 usa o preço da assinatura
  (US$ 0,423/Mtok = US$ 0,05 por ponto da janela semanal) com a parte **sólida** = token
  MEDIDO pelo motor e a **hachurada** = estimativa pela régua de tokens do modelo (média
  medida no motor; prévia do A/B só onde o motor nunca mediu). Honestidade do número: só
  **12 das 102 chamadas** têm token medido (a instrumentação da P-10 é de 28/09 à noite) —
  o gráfico marca o que é medição e o que é estimativa. A tabela final lista o objetivo de
  cada um dos **36 pacotes** das 4 sprints (lido do `dag.json`, não escrito à mão). O texto
  passou a incluir a 003 (a P01 rodou) e o PDF está com **9 páginas**.
- **28/09** — **P-01 resolvida: 002 e 004 mergeadas em `main` e publicadas.** Os dois
  merges foram `--no-ff`, resolvendo os conflitos **somando os dois lados**: `README.md`
  (seção da tela + seção do Planejador), `autodev/cli.py` (`cmd_desbloquear` + `cmd_plan`),
  `plan_prompt.py` (regra do D-23) e `test_plan_docs.py` (conserto do P02/P04 da 004).
  Armadilha pega pela própria suíte: a "soma" do `README.md` tinha posto o `## Planejador`
  **dentro** de `## Como rodar` e a 004 acrescentou o dela no lugar certo → **duplicado**;
  resolvido ficando com a versão da 004 (é superset e tem teste de estrutura próprio). No
  `decisions.md` da 004 os **dois** registros foram preservados (a numeração das tasks
  colide com a do sprint, com nota explicando o porquê). Suíte depois dos merges: **325
  verdes** (219 + os testes que as duas sprints trazem). Push: **`782d8c3..2620aba`** —
  `main` sincronizada com `origin/main`.
- **28/09** — **P-03 resolvida: DEVFACTORY-003 em execução.** Autor aprovou o objetivo;
  plano revalidado (`verificar_plano.py`: 7 tasks, 0 erros/0 avisos) e rodada 1 iniciada às
  **22:58** (`--rodadas 5`). P01 tentativa 1 = `codex gpt-5.6-luna/low` (degrau barato,
  como manda a matriz): 54.069 tokens, testes `exit=0 passed=4 failed=0`.
- **28/09** — **P-04 resolvida: sprint 1 ratificada (HAQ-002 fechada).** Autor autorizou o
  encerramento com o método de conclusão retroativa. Verificado antes de fechar (e gravado
  no próprio registro da HAQ): `sprint_state` = `ENCERRADO`; **15/15** tasks `DONE`;
  tentativas com `origem` = **14 `retroativo`** + 1 `aceitacao_real`; `SPRINT-REPORT.md`
  presente; D-13 registrada. Fechada pelo caminho do motor (`StateStore.haq_resolver`, que
  grava o evento `haq_resolvido` e commita) — não por `UPDATE` solto no banco.
- **28/09** — **P-07 resolvida: revisão retroativa da sprint 2 ratificada.** As baixas
  registradas (2 itens de `README` fechados pela P03 da 004; diferença 203×205 explicada em
  D-22) foram aprovadas e a dúvida do `dag.json` foi **verificada com comando**: o
  `dag.json` de `main` é idêntico ao de antes do merge (o merge não o tocou) e diferente da
  evidência `dag.json.antes-da-correcao-D16` — as correções do D-16/D-19 seguem de pé.
  Registro: seção datada em `.autodev/sprints/DEVFACTORY-002/decisions.md`.
- **28/09** — **P-08 resolvida: vigia da 003 no ar, com gate de monitor.** O vigia
  `c9ff58c7925d` roda `every 30m` **24 h** (não precisa de um segundo job "diurno") e o
  campo `monitor` compara a saída de `.autodev/scripts/estado_003.sh` entre ticks: **só
  acorda o agente quando o estado muda** — silêncio quando nada muda. O monitor saiu
  determinístico (sem hora/data/idade no texto) e ganhou um **detector de travamento**
  (log parado >20 min vira `SEM SINAL`), senão o gate ficaria cego para um motor travado. O
  vigia "diurno" antigo (`329c5e1d03b4`) segue **pausado de propósito**: ele retomava a
  **sprint 2**, encerrada e mergeada — retomá-lo queimaria uma sessão de agente a cada 30
  min para não fazer nada. **Ao fechar a 003, pausar o vigia dela.**
- **28/09** — **Padrão de escolha de modelos (autor):** Codex **sempre no `low`** (qualquer
  modelo), DeepSeek **sempre no `high`** (flash e pro). Registrado no plano do A/B (seção
  0), na skill `llm-coding-efficiency` e aqui. Aplicação no motor: **P-13** (aberta).
- **28/09** — **Relatório da prévia do A/B** passa a sair **ordenado por preço** e com
  `Δ5h`/`Δsemanal` em **2 casas decimais** (derivadas dos tokens medidos, porque o RPC
  devolve ponto inteiro); há tabela separada só com os braços **dentro do padrão**.
- **28/09** — **P-09 corrigida**: classe de falha declarada sem escalonamento deixou de
  gastar degrau. A decisão agora carrega o **tier atual** explícito (`cfg.tier_atual`) nos
  blocos de ambiente e de `classes_sem_escalonamento`, e o orquestrador obedece o degrau
  **da decisão** (`Orquestrador._modelo_da_tentativa`) em vez de deduzi-lo do contador da
  task. Testes novos em `.autodev/tests/test_escalonamento_revisao.py` (falhavam antes):
  **219 verdes** então. Commit **`0875764`**.
- **28/09** — **Relatório de tentativas** ganhou **`retrab. bruto` e `retrab. ajust.`** em
  múltiplo (`0,3x`, `2x`, `3x`; não em %, porque a razão "reprovações por aprovação" passa
  de 1x): global **0,5x bruto → 0,4x ajustado**; o `002/P09` sai de 0,8x para **0x** quando
  se desconta a culpa do nosso teste/plano. Também corrigido o layout: cabeçalho da tabela
  repete entre páginas e nenhuma linha se parte. Commits `cef0b2f`, `060cf1c`.
- **28/09** — **Prévia de custo do A/B medida** (12 braços; ver P-11). Tabela de preço do
  Codex derivada da assinatura do autor; custos do DeepSeek do `--usage-file`; saldo lido
  direto na API. Método guardado na skill `llm-coding-efficiency`.
- **28/09** — **Tier "Fast" removido** do `~/.codex/config.toml` (`service_tier = "priority"`,
  1,5–2× de consumo, só por velocidade). Testado antes: `flex` **não é aceito** por esses
  modelos (o CLI omite com aviso), então o mais barato é **não pedir tier nenhum** — vai no
  padrão. Backup em `~/.codex/config.toml.bak-20260928`.
- **28/09** — **P-02 / DEVFACTORY-004 concluída**: 4/4 tasks integradas (P01, P02, P03,
  P04) com os 4 portões (build, testes, lint, segurança) OK em cada integração; 23 chamadas
  do Codex, 1 espera de cota, 0 HAQ. Decisões novas: D-23 (comando de teste é o que o
  runner resolve), D-24 (rodada única é do motor), D-25 (portão de segurança julga o diff).
  Com isso fecharam os achados **P-05** (seção `## Planejador` rebaixada + teste que prova
  menos do que anuncia) e **P-06** (forma do comando nos critérios), além dos 2 itens de
  `README` da P-07.
- **28/09** — **P-10 concluída**: tokens por tentativa gravados (`attempts.tokens_total` +
  `tokens_fonte`), via opt-in `ASK_CODEX_USO` no wrapper `~/.local/bin/ask-codex`; suíte com
  `test_tokens.py` (15). Retroativo **não** é recuperável (3/110 = 2,7% medidos). Commit
  `078139e`.
- **28/09** — DEVFACTORY-002 encerrada: 10/10 integradas, suíte do projeto 205 testes,
  motor 194; quatro portões OK em cada integração.
- **28/09** — D-22: revisões retroativas por LLM da P09/P10 refeitas com créditos repostos;
  cobertura da P09 provada por medição (186 → 201 coletados).
- **28/09** — D-19/D-20: preservação **com evolução** (a regra absoluta do D-16 tornava a
  P09 impossível) e espera de cota pelo reset informado pelo agente.
- **28/09** — D-18: worktree sempre na base atual e `run` em passadas (`--rodadas`).
- **27/09** — HAQ-001 resolvida (credencial do Codex montada somente leitura, sem cópia);
  D-16 (DAG da sprint 2, causa-raiz das 9 tasks bloqueadas) e D-17 (entrega vazia).
- **27/09** — DEVFACTORY-001 encerrada com conclusão retroativa (ratificação formal em
  28/09: ver P-04).
