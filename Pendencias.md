# Pendências do Coding_Machine

Atualizado em **28/09/2026** (Hermes), logo depois de a `DEVFACTORY-002` ser encerrada.
Estado dos sprints: **001 ENCERRADA** · **002 ENCERRADA (10/10 integradas)** ·
**003 PLANEJADA** · **004 PLANEJADA (aprovada pelo autor em P1, em execução)**.

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

### P-01 · Merge da DEVFACTORY-002 em `main` — **autor**
- **Por que importa:** o trabalho está integrado, mas fora do `main`. Enquanto não
  mergear, `main` não tem o planejador, o portão de testes nem a tela.
- **Caminhos:**
  - branch com o trabalho: `sprint/DEVFACTORY-002/integration`
  - arquivos que **vão conflitar** (as duas pontas os mudaram):
    `.autodev/sprints/DEVFACTORY-002/decisions.md` e `README.md`
  - resolução correta: **somar os dois lados** (o log de decisões de um lado, as duas
    seções de documentação do outro) — não escolher uma versão.
- **Verificação:**
  `git merge-tree --write-tree main sprint/DEVFACTORY-002/integration` (lista os conflitos)
- **Pronto quando:** `main` contiver as 10 tasks e os dois arquivos com as duas partes.
- **Já conferido:** `dag.json` **não** regride — só o `main` o mudou desde a base do
  merge, então as correções D-16/D-19 são preservadas.
- **Extra:** há commits locais à frente de `origin/main` (sem push). Push é decisão sua.

### P-02 · DEVFACTORY-004 — correções da revisão retroativa — **agente (aprovada)**
- **Decisão:** autor escolheu P1 em 28/09: roda a 004 agora e emenda na 003.
- **Caminhos:** `.autodev/sprints/DEVFACTORY-004/` (`spec.md`, `dag.json`, `sprint.yaml`)
- **Verificação:** `cd .autodev/sprints/DEVFACTORY-004 && ../../../.venv/bin/python -m autodev --sprint DEVFACTORY-004 status`
- **Pronto quando:** as 4 tasks (P01..P04) integradas, com os 4 portões OK.
- **Achados que ela fecha:** os 2 médios da P10 (P-05) e as baixas (P-07).

### P-03 · Aprovar o objetivo da DEVFACTORY-003 — **autor**
- **Por que importa:** é a sprint que transforma a lição do D-16 em código — colisão de
  arquivo na mesma onda vira **erro** antes de o agente rodar — e cria o portão de
  aprovação humana do plano. Sem o seu "vai", ela não começa.
- **Caminhos:** `.autodev/sprints/DEVFACTORY-003/` (`spec.md`, `dag.json`, `sprint.yaml`)
- **Verificação:**
  `.venv/bin/python .autodev/scripts/verificar_plano.py DEVFACTORY-003` (hoje: 0 erros, 0 avisos)
- **Pronto quando:** você aprovar (ou ajustar) o objetivo. Depois disso eu rodo, em
  sequência à 004.
- **Ordem importa:** 004 **antes** de 003 — a P07 da 003 mexe em
  `.autodev/tests/test_plan_docs.py`, o mesmo arquivo que a P02 da 004 corrige.

### P-04 · HAQ-002 — encerramento retroativo da sprint 1 — **autor**
- **O que é:** as T01–T14 da sprint 1 foram concluídas por **evidência retroativa**
  (código escrito fora do laço do orquestrador, marcado com `origem='retroativo'`).
  Falta você autorizar (ou não) esse encerramento.
- **Caminhos:**
  - `.autodev/sprints/DEVFACTORY-001/SPRINT-REPORT.md` (leitura)
  - `.autodev/sprints/DEVFACTORY-001/decisions.md` — decisão **D-13**
  - linha `HAQ-002` na tabela `haq` do `.autodev/state.db` (`status = OPEN`)
- **Verificação:** `.venv/bin/python -m autodev haq`
- **Pronto quando:** a HAQ estiver com resultado registrado (o sprint 1 já está
  `ENCERRADO`; a HAQ é a ratificação formal do método).

### P-05 · Os 2 achados médios da revisão retroativa da P10 — **agente (via 004)**
- **1.** `README.md:91` — a seção `## Planejador` foi inserida **dentro** de
  `## Como rodar`, rebaixando `### Ciclo de vida do sprint` e `### Conclusão por
  evidência` a subseções do Planejador.
- **2.** `.autodev/tests/test_plan_docs.py:13` — `_secao_planejador()` corta a seção
  apenas no próximo `\n## `, então o teste examina subseções que não são do Planejador:
  **prova menos do que anuncia**.
- **Quem fecha:** `P01` e `P02` da DEVFACTORY-004.
- **Evidência:** `.autodev/sprints/DEVFACTORY-002/revisao-retroativa/P10-veredito-relido.json`

### P-06 · Forma do comando de teste nos critérios — **agente**
- **O que é:** o critério (`teste` e os comandos citados) é executado pelo **runner do
  motor**, com `cwd` **dentro do worktree**. Ali o venv do projeto **não existe de forma
  útil**, então `.venv/bin/python -m pytest` quebra o portão de testes. A forma
  suportada é **`python3 -m pytest ...`** — o sandbox resolve o interpretador do
  projeto. Rodar à mão é outra coisa: aí sim vale `.venv/bin/python -m pytest` (é o que
  o README manda).
- **Provado na prática em 28/09:** os critérios da DEVFACTORY-004 saíram com
  `.venv/bin/python` e **12 tentativas** (P01 e P04) morreram com
  `No module named pytest` — evidência em
  `.autodev/sprints/DEVFACTORY-004/evidence/P04-*.txt` e decisão **D-23** em
  `.autodev/sprints/DEVFACTORY-004/decisions.md`.
- **Caminhos:** `.autodev/sprints/*/dag.json` (campo `teste` e critérios),
  `.autodev/sprints/*/sprint.yaml` (bloco `aceitacao`), `autodev/plan_prompt.py`.
- **Verificação:** `grep -rn "\.venv/bin/python" .autodev/sprints/*/dag.json` (só deve
  aparecer onde o texto **proíbe** a forma) e
  `grep -rn "python3 -m pytest" .autodev/sprints/*/dag.json`
- **Quem fecha:** `P04` da DEVFACTORY-004 — já invertida: exige a forma do runner e
  reprova caminho de venv relativo, com a explicação.

### P-07 · Baixas da revisão retroativa (registradas, não bloqueiam) — **agente**
- `README.md:105` — diz que a execução "consome cota do agente configurado", mas o
  agente do planejador é **fixo** em `codex` (`autodev/cli.py`, `cmd_plan`). → P03 da 004.
- `README.md:167` — "Suíte do orquestrador: **123** testes passando": número obsoleto
  (a árvore coleta 205). → P03 da 004.
- Evidência anexada à tentativa da P10 dizia "203 passed"; a árvore revisada coleta
  **205**. Diferença explicada (a tentativa rodou antes do último ajuste), registrada
  em D-22.
- `dag.json` do worktree da P09 estava defasado em relação ao do repo (o motor lê o do
  repo; o do worktree veio da base do branch). Não afeta entrega; conferido que o merge
  **não** regride o `dag.json` (ver P-01).
- **Evidência:** `.autodev/sprints/DEVFACTORY-002/revisao-retroativa/P0{9,10}-veredito-relido.json`

### P-08 · Higiene de vigias — **agente**
- O vigia noturno terminou seus 14 disparos e o diurno foi **pausado** quando a sprint 2
  fechou (`cronjob` `329c5e1d03b4`). Se a 003 for aprovada, criar um vigia novo para ela
  (mesmo padrão: trava de sobreposição via `.autodev/scripts/rodada_em_andamento.py` e
  silêncio quando nada muda).

---

## Relatórios e produtos por sprint

Caminhos relativos à raiz do projeto (`~/Code/Coding_Machine`), salvo indicado.

### DEVFACTORY-001 — laço autônomo (ENCERRADA)
| produto | caminho |
| --- | --- |
| spec / plano | `.autodev/sprints/DEVFACTORY-001/{spec.md,plan.md}` |
| plano executável | `.autodev/sprints/DEVFACTORY-001/dag.json` |
| decisões | `.autodev/sprints/DEVFACTORY-001/decisions.md` (D-13 = conclusão retroativa) |
| HAQ | `.autodev/sprints/DEVFACTORY-001/HAQ.md` |
| **relatório final** | `.autodev/sprints/DEVFACTORY-001/SPRINT-REPORT.md` |
| logs | `.autodev/sprints/DEVFACTORY-001/logs/` |

### DEVFACTORY-002 — planejador: do prompt ao plano executável (ENCERRADA, 10/10)
| produto | caminho |
| --- | --- |
| spec | `.autodev/sprints/DEVFACTORY-002/spec.md` |
| plano executável | `.autodev/sprints/DEVFACTORY-002/dag.json` |
| planos anteriores (evidência) | `…/dag.json.antes-da-correcao-D16` e `…-D19` |
| decisões | `.autodev/sprints/DEVFACTORY-002/decisions.md` (D-01 … D-22) |
| **relatório final** | `.autodev/sprints/DEVFACTORY-002/SPRINT-REPORT.md` |
| evidência de teste (76) | `.autodev/sprints/DEVFACTORY-002/evidence/` |
| logs do motor | `.autodev/sprints/DEVFACTORY-002/logs/orquestrador.log` (+ por tentativa e revisão) |
| **revisão retroativa por LLM** | `.autodev/sprints/DEVFACTORY-002/revisao-retroativa/` (vereditos, logs, scripts) |
| branch de integração | `sprint/DEVFACTORY-002/integration` (P01 `b963e0e` … P10 `785988d`) |
| branches por task | `sprint/DEVFACTORY-002/P0X-*`; tentativas antigas em `refs/arquivo/DEVFACTORY-002/` |
| logs das rodadas (fora do repo) | `~/workspace/a_devfactory/sprint-002-retomada/` |

### DEVFACTORY-003 — portão de qualidade do plano (PLANEJADA)
| produto | caminho |
| --- | --- |
| spec / plano / sprint | `.autodev/sprints/DEVFACTORY-003/{spec.md,dag.json,sprint.yaml}` |

### DEVFACTORY-004 — correções da revisão retroativa (PLANEJADA → em execução)
| produto | caminho |
| --- | --- |
| spec / plano / sprint | `.autodev/sprints/DEVFACTORY-004/{spec.md,dag.json,sprint.yaml}` |

### Ferramentas do repositório (não são de um sprint)
| produto | caminho |
| --- | --- |
| **tela de eventos** (permanente) | `./tela.sh` → `.autodev/scripts/tela.py` |
| verificador de planos | `.autodev/scripts/verificar_plano.py` |
| guarda de sobreposição (vigias) | `.autodev/scripts/rodada_em_andamento.py` |
| **relatório de tentativas** (visual, regenerável) | `REPORT-TENTATIVAS-CODEX.md` + `report/` (script, PNGs, HTML, PDF A4) |
| testes do motor | `.venv/bin/python -m pytest .autodev/tests/ -q` |
| estado (fonte da verdade) | `.autodev/state.db` |

---

## Resolvidas (histórico curto)

- **28/09** — DEVFACTORY-002 encerrada: 10/10 integradas, suíte do projeto 205 testes,
  motor 194; quatro portões OK em cada integração.
- **28/09** — D-22: revisões retroativas por LLM da P09/P10 refeitas com créditos
  repostos; cobertura da P09 provada por medição (186 → 201 coletados).
- **28/09** — D-19/D-20: preservação **com evolução** (a regra absoluta do D-16 tornava
  a P09 impossível) e espera de cota pelo reset informado pelo agente.
- **28/09** — D-18: worktree sempre na base atual e `run` em passadas (`--rodadas`).
- **27/09** — HAQ-001 resolvida (credencial do Codex montada somente leitura, sem cópia);
  D-16 (DAG da sprint 2, causa-raiz das 9 tasks bloqueadas) e D-17 (entrega vazia).
- **27/09** — DEVFACTORY-001 encerrada com conclusão retroativa (pendente de ratificação:
  ver P-04).
