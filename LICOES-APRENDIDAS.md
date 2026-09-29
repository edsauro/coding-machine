# Lições aprendidas — DEVFACTORY / Coding_Machine

**Data:** 29/09/2026 · **Escopo:** sprints DEVFACTORY-001 a 004
**Como ler:** cada achado traz a evidência que o sustenta (arquivo, linha, número).
Nada aqui é impressão — o que não tem evidência está marcado como *hipótese*.
**Por que existe:** o autor parou o desenvolvimento em 29/09 ao concluir que
"tem erro demais, problema demais". Este documento é a revisão que ele pediu.

---

## 1. Sumário executivo

1. **O gargalo não é o modelo. É o contrato.** De 147 findings de rejeição, **75%
   são defeito de contrato ou de plano**; apenas **3% (5 findings) são da
   implementação**. Escalar modelo num contrato quebrado paga caro para receber
   a mesma falha.
2. **Os verificadores certos já existem — e o laço nunca os chama.** `verificar_plano.py`
   e o novo `checar_dono_arquivo.py` são scripts manuais. Não são portões.
3. **Não existe portão entre fases.** Existem 4 portões *por task*, na integração
   (build, lint, segurança, aceitação). Nada valida o plano antes de gastar cota,
   e nada impede uma sprint inteira de avançar sobre um contrato defeituoso.
4. **Produtividade real: 50%.** 139 chamadas; 69 terminaram em entrega aprovada;
   42 (30%) morreram em REVIEW_FAILURE.
5. **Os ativos de apoio levantados pelo autor nunca foram usados.** 28 repos
   analisados, BMAD instalado, superpowers instalado como skill do Codex —
   **zero menção** em qualquer spec, decisions ou dag de sprint.

---

## 2. O que foi construído (contexto)

O motor (`autodev/`, 17 módulos) executa um backlog de sprint: pega as tasks do
DAG, cria um worktree isolado por task, chama um agente de código (Codex/AGY) num
sandbox `bubblewrap`, roda os testes, submete a um revisor independente e integra
num branch de sprint — **nunca em `main`**.

| sprint | tarefas | resultado |
|---|---|---|
| DEVFACTORY-001 | 15 | 15/15, mas **14 por evidência retroativa** (`origem='retroativo'`) |
| DEVFACTORY-002 | 10 | **1/10 integradas**, 9 bloqueadas por conflito |
| DEVFACTORY-003 | 7 | **7/7** |
| DEVFACTORY-004 | 4 | em andamento |

---

## 3. Os números (fonte: `.autodev/state.db`)

| sprint | chamadas | aprovadas | REVIEW_FAILURE | cota |
|---|---:|---:|---:|---:|
| 001 | 15 | 15 | 0 | 0 |
| 002 | 73 | 43 | 17 | 5 |
| 003 | 27 | 7 | 18 | 1 |
| 004 | 24 | 4 | 7 | 2 |
| **total** | **139** | **69 (50%)** | **42 (30%)** | **8** |

### 3.1 As 25 rejeições das sprints 003/004, por causa raiz

| causa raiz | findings | % |
|---|---:|---:|
| PLANO: dono de arquivo | 41 | 28% |
| CONTRATO: fato volátil | 33 | 22% |
| CONTRATO: critério ambíguo | 29 | 20% |
| CONTRATO: comando/runner | 7 | 5% |
| não classificado | 32 | 22% |
| **IMPLEMENTAÇÃO** | **5** | **3%** |

*Ressalva:* os 22% "não classificado" vêm de classificador por palavra-chave e
precisam de leitura manual antes de virarem regra.

### 3.2 O revisor não é o problema

Quem recusou **24 das 25 vezes** foi `hermes/deepseek-flash` — o revisor mais
barato da casa (US$ 0,0027/chamada). Os findings são substantivos e corretos:
número falso no README ("contagem atual: 325" com 426 testes passando), contradição
com o `state.db`, acoplamento frágil em teste, arquivo pertencente a outra task.
**Trocar o revisor por um modelo mais forte não muda nada disso.**

---

## 4. Causas raiz, com evidência

### 4.1 O contrato pede fato volátil (22%)

O critério do P07 exigia *"a contagem de testes"* e *"o estado das sprints"*
gravados no README. São fatos que **mudam no commit seguinte**: a implementação
correta envelhece e o revisor a chama de falsa — corretamente. O P07 foi reprovado
4 vezes por isso, e na 5ª foi aprovado com a ressalva de que a seção "não traz
número nenhum — é um desvio literal do critério". O implementador havia feito a
escolha de engenharia certa (citar o comando de medição em vez do número) e foi
punido por um critério defeituoso.

### 4.2 O plano não controla quem escreve o quê (28%)

Cruzando os arquivos que cada task **realmente tocou** (coluna `changed_files` do
banco) com as outras tasks do mesmo sprint:

- **DEVFACTORY-003:** 4 arquivos tocados por 2+ tasks — `autodev/cli.py` (P01, P04,
  P05), `autodev/planner.py` (P01, P02), `autodev/config.py` (P02, P05),
  `.autodev/tests/test_plan_aceitacao.py` (P01, P05). **Nenhum** coberto por
  dependência.
- **DEVFACTORY-004:** outros 4 — `README.md`, `decisions.md`, `test_plan_docs.py`,
  `fixtures/readme_estrutura/planejador.md`. **Nenhum** coberto por dependência.

O revisor pegou o caso do P07 editando `.autodev/tests/test_readme_estrutura.py`,
"o oráculo congelado que pertence a OUTRA task".

### 4.3 O verificador checa a declaração; o revisor checa o fato

`verificar_plano.py` extrai os arquivos **do texto dos critérios**. Ele reporta
`DEVFACTORY-003: 0 erro(s), 0 aviso(s)` — e mesmo assim existem 4 colisões reais
no mesmo sprint. **O critério é uma promessa; o agente a quebra em silêncio.**

*Novo instrumento criado nesta revisão:* `.autodev/scripts/checar_dono_arquivo.py`
— compara o diff real (base da 1ª tentativa → commit aprovado) com os arquivos
declarados pelas outras tasks, respeitando dependência transitiva (regra A2).
Rodado no histórico, encontra **5 violações na 003** e 0 na 004.

*Ainda em aberto:* a colisão sobre o fato (4.2) **não** tem veredito automático —
`README.md` e `decisions.md` são tocados por todas as tasks legitimamente, então a
regra precisa de isenção explícita ou de critério de preservação (regra A1).

### 4.4 Defeito de contrato queimou 12 tentativas (5% + parte do "não classificado")

D-23: os critérios mandavam rodar `.venv/bin/python -m pytest`, mas o runner
executa **dentro do worktree**, onde esse venv não existe → exit=1, "No module
named pytest". P01 e P04 da sprint 004 queimaram **12 tentativas** (6 + 6) por isso.

### 4.5 O motor já sabe que está repetindo — e não para

`retry.mesmo_lugar()` (limiar de similaridade 0.85) detecta "a mesma falha de novo".
Hoje ele é usado **num único lugar**: `orchestrator.py` linha 143, e só a partir da
**4ª tentativa**, e apenas para **trocar de agente** — nunca para parar.

Medido: na sprint 004, P01 repetiu a falha já na 2ª tentativa e seguiu até a 9ª;
P04 repetiu na 2ª e seguiu até a 10ª. **15 chamadas foram gastas depois de a falha
já ter se repetido** — e a causa era o defeito de contrato do item 4.4.

### 4.6 Retentar com a mesma entrada, esperando saída diferente

`integrar()` chama `forcar_estado(RETRY)` direto, sem classe de falha e sem exigir
evidência nova — ignorando o conceito `requer_evidencia_nova` que já existe no
motor. Consequência medida na sprint 002: **1h25min dos 2h28 de janela foram
repetição inútil**.

---

## 5. Portões de qualidade: o que existe e o que falta

**Existem (e rodam, por task, na integração):**
`_portao("build")`, `_portao("lint")`, `_portao_seguranca()`, `portao_aceitacao()`
— `autodev/integration.py` linhas 118, 138, 143, 265. O `.githooks/pre-commit`
está ativo (`core.hooksPath = .githooks`) com 3 checagens: segredos, arquivos
proibidos e contexto pessoal/saúde.

**Não existem:**
- portão **antes** de gastar cota (nada valida o plano contra o verificar de planos)
- portão de **entrada de fase** (plano → implementação → integração → encerramento)
- portão que **pare** a sprint quando a integração falha (as ondas seguintes continuam)
- portão de **dono de arquivo** sobre o diff real
- qualquer verificação de que o HAQ foi respondido antes de encerrar

**A causa é estrutural, não de disciplina:** os verificadores são scripts
(`.autodev/scripts/`), e o orquestrador **não os invoca em lugar nenhum** —
verificado por `grep` em `autodev/*.py`. Não há como serem respeitados: eles não
estão no caminho de execução.

---

## 6. Ativos levantados pelo autor que nunca foram usados

### 6.1 Os 28 repositórios

A análise existe e está em `~/workspace/bot_github/` (`_RESUMO.md`,
`_CAPABILITIES.md`, mais os relatórios individuais). O `_CAPABILITIES.md` aponta
candidatos **exatos** para as lacunas encontradas nesta revisão:

| lacuna real | candidato já levantado |
|---|---|
| critérios de teste / avaliação | `addyosmani/agent-skills`, `tech-leads-club/agent-skills` |
| plano e workflow por fases | `bmad-code-org/BMAD-METHOD`, `sandeco/reversa` |
| orquestração e verificação | `obra/superpowers` |
| planejamento com arquivos | `OthmanAdi/planning-with-files`, `mattpocock/skills` |

**Uso no projeto: zero.** `grep` por bmad / superpowers / claude / anthropic em
todas as `spec.md`, `decisions.md` e `dag.json` das sprints → **0 arquivos**.

### 6.2 O superpowers já está instalado

`~/.codex/skills/` contém `writing-plans`, `executing-plans`,
`verification-before-completion`, `requesting-code-review`,
`receiving-code-review`, `test-driven-development`, `using-git-worktrees`,
`dispatching-parallel-agents`. São **exatamente** as fases que faltam como portão.
Está disponível, ao alcance, e não é chamado por nada.

### 6.3 O planejador construído na sprint 002 nunca rodou

`autodev plan` existe, está no `main`, tem 137 testes verdes — e **não existe um
único `plano-*.log`** em sprint nenhuma. Os contratos das sprints 003 e 004 foram
**escritos à mão**, sem verificação sistemática. A pergunta do autor ("imaginei que
foi elaborado com o modelo mais simples da OpenAI") tem resposta: **não foi modelo
nenhum** — foi escrita manual. É por isso que o D-23 (4.4) passou.

---

## 7. O que já foi corrigido vs. o que recorre

| defeito | estado |
|---|---|
| D-07: worktree não via o trabalho da dependência | **corrigido** (`782d8c3`) — teste de reprodução passa |
| Integração rodava uma vez no fim, não por onda | **corrigido** (mesmo commit) |
| 11 cópias do token OAuth na máquina | **corrigido** → restam 2 (a original e a do sandbox-home) |
| Cota do AGY rotulada como "Codex" | registrado (D-08/D-09), **não corrigido** |
| Contexto pessoal/de saúde no repo público | **corrigido** + 3ª checagem no hook |
| Contrato exigindo fato volátil | **recorre** — nenhuma regra existe |
| Dono de arquivo sobre o diff real | **recorre** — verificador criado agora, ainda não é portão |
| Parar na falha repetida | **recorre** — mecanismo existe, não é usado |
| Retentar sem evidência nova | **recorre** — `integrar()` ignora a regra |
| Nada valida o plano antes de gastar cota | **recorre** — sem portão |

---

## 8. Ações corretivas, por retorno

**(1) Portão de entrada de fase — o mais estrutural.**
O orquestrador passa a invocar `verificar_plano.py` **antes** da primeira chamada
de agente e aborta se houver erro. Determinístico, custo zero, e é o que
transforma os verificadores de "script que eu esqueço" em portão.

**(2) Parar na primeira falha repetida.**
Usar o `mesmo_lugar()` que já existe para ir a `WAITING_HUMAN` em vez de queimar
tentativas. Evidência: 15 chamadas na 004. Recomendação: `max_tentativas = 2`.

**(3) Duas regras novas no verificador de planos** (ambas determinísticas, custo zero):
- **fato volátil:** critério que exige número que muda (contagem de testes, estado
  de sprint) é erro de contrato.
- **dono de arquivo:** estender `checar_dono_arquivo.py` para rodar no portão de
  integração, não só à mão.

**(4) Tornar o revisor de contrato independente.**
Ninguém revisa o próprio contrato. Hoje o contrato é escrito, os agentes gastam
cota, e só então o erro aparece — 4 vezes seguidas no mesmo caso.

**(5) Usar o que já está instalado.**
`writing-plans` / `verification-before-completion` do superpowers cobrem as fases
1 e 4. Antes de escrever qualquer coisa nova, ler esses dois e o `_CAPABILITIES.md`.

---

## 9. Decisões abertas (autor)

1. **Todos os modelos em `luna/low`** — instrução dada e **aplicada** em
   `models.yaml`. Consequência: a escada deixa de escalar modelo, o par
   `escada_5`/`tres_degraus` fica indistinguível, `cfg.escalonou()` passa a
   devolver sempre `False` (o contador `escalonamentos` zera) e **10 testes**
   que codificam a matriz antiga falham. Reescrevê-los é decisão de desenho:
   eles devem passar a afirmar o **mecanismo** (o tier sobe) e não o **catálogo**
   (qual slug). Aguardando confirmação.
2. **Número de tentativas antes de rever contrato** — recomendação: **2**,
   parando na primeira falha repetida (item 8.2). Hoje está em 5.
3. **Redigir os planos à mão ou pelo planejador** — critério de escolha proposto:
   ver seção 10.
4. **Contrato: revisar, refazer ou refazer do zero** — critério proposto: seção 10.
5. **Pagar API do Claude/Anthropic para redigir/revisar contratos** — avaliar
   contra o que já está instalado e contra o custo medido (seção 11).

---

## 10. Critério proposto: refazer, revisar ou refazer do zero

Não existe resposta por sensação. Proposta, ancorada no que já medimos:

| situação | ação | por quê |
|---|---|---|
| contrato com fato volátil ou critério sem arquivo | **corrigir o contrato** | defeito localizado, determinístico |
| mesmo finding repetido em 2 tentativas | **parar e revisar o contrato** | a entrada não mudou materialmente |
| 2+ tasks colidindo em arquivo sem dependência | **refazer o PLANO** (não o contrato) | é defeito de decomposição |
| mesma classe de falha em 2+ sprints seguidas | **refazer do zero** | o problema é o método, não a instância |
| contrato já passou pelo portão e a task falhou 2× | **revisar a implementação**, não o contrato | aí o defeito é de código |

O último caso importa: hoje o motor trata tudo como defeito de código e escala
modelo. A coluna "ação" é o que falta — **classificar a falha e escolher o
instrumento**, em vez de retentar.

---

## 11. Sobre comprar API do Claude/Anthropic

Antes de assinar, três fatos medidos:

- **O revisor mais barato da casa já acha os defeitos.** 24 das 25 recusas vieram
  do `deepseek-flash/high` (US$ 0,0027/chamada) e os findings são corretos. O
  problema é que **o defeito está no contrato**, e o revisor de código não é o
  instrumento para julgar um contrato.
- **O que falta é um revisor de CONTRATO**, com um checklist determinístico —
  não um modelo mais caro olhando o mesmo texto solto.
- **Custo comparado (medido no estudo de eficiência, pacote mínimo, 1 chamada):**
  `astra/high` US$ 0,0094 · `deepseek-v4-pro/high` US$ 0,0133 · `deepseek-flash/high`
  US$ 0,0027. Nenhum deles tem **capacidade** medida — o estudo mediu custo e tempo,
  não acerto. Chamar qualquer um de "o mais forte" não tem lastro nos dados.

**Recomendação:** não pagar ainda. Primeiro fechar o portão de entrada de fase
(item 8.1) e as duas regras determinísticas (item 8.3) — isso resolve a maior parte
sem custo por chamada. Se depois disso ainda houver classe de defeito que só
raciocínio pega, aí o gasto tem alvo definido e dá para medir o retorno. Começar
com um **A/B de 2 chamadas**: mesmo contrato, uma revisão com cada candidato,
comparar os findings.

---

## 12. O que este documento não afirma

- Não afirma que os 22% "não classificados" sejam de contrato — são desconhecidos.
- Não afirma qual modelo é "o mais forte" — o estudo mediu custo, não acerto.
- Não afirma que o superpowers ou o BMAD resolveriam — afirma que **não foram
  testados**, apesar de instalados e apesar de a análise dos 28 repos os apontar
  exatamente para as lacunas encontradas.
