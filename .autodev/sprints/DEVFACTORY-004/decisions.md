# DEVFACTORY-004 — decisões

## D-23 — O comando dos critérios é o que o RUNNER resolve, não o que roda no terminal

**Achado (28/09, logo na primeira rodada da sprint):** P01 e P04 queimaram **12
tentativas** (6 + 6) com `testes exit=1 passed=None failed=None`. Não era falta de
capacidade dos modelos — era **defeito de contrato meu**.

**A cadeia do erro:**

1. A revisão retroativa da sprint 2 apontou, com razão, que o comando escrito nos
   critérios (`python3 -m pytest`) **não roda no terminal desta máquina** — aqui o
   `python3` do sistema não tem pytest.
2. Eu apliquei esse achado **fora do contexto dele**: troquei todos os critérios e o
   campo `teste` para `.venv/bin/python -m pytest`. O revisor estava falando de rodar
   o comando *à mão*; os critérios são executados pelo **runner do motor**.
3. O runner roda o comando com `cwd` **dentro do worktree** (`autodev/testrunner.py`,
   `rodar()`), e o worktree não tem venv utilizável. Pior: o critério *mandou* os
   agentes criarem um venv ali — os worktrees amanheceram com
   `.venv/bin/python -> .../uv/python/...` sem site-packages.

**Evidência** (`.autodev/sprints/DEVFACTORY-004/evidence/P04-*.txt`):

```
$ .venv/bin/python -m pytest .autodev/tests/test_plano_comando.py -q
exit=1 (0.0s)

/home/saurus/Code/Coding_Machine/.autodev/worktrees/DEVFACTORY-004-P04-codex/.venv/bin/python:
No module named pytest
```

**A forma suportada é `python3 -m pytest ...`:** o sandbox resolve o interpretador do
projeto (o venv e o `sys.prefix` são bindados — `autodev/sandbox.py`, ver o comentário
das linhas 57-79). Para rodar à mão, fora do motor, o README manda
`.venv/bin/python -m pytest`.

**Correção:**

- critérios e `teste` das 4 tasks voltam para `python3 -m pytest ...`, com a proibição
  de caminho de venv relativo escrita no próprio critério;
- a **P04** (que ia propagar o erro para o verificador de planos e para o prompt de
  planejamento) passa a fazer o **inverso**: exigir a forma do runner e reprovar
  `.venv/bin/python`, explicando o motivo;
- `dag.json` anterior preservado em `dag.json.antes-da-correcao-D23`.

**Reabertura:** P01 e P04 voltaram com **contador 0** (escada justa, do degrau mais
barato), worktrees e branches recriados na base da integração; o trabalho anterior
(que existia — os agentes tinham commitado README reestruturado) ficou arquivado em
`refs/arquivo/DEVFACTORY-004/` como evidência, e os `.venv` espúrios foram removidos.

**Lição para o motor:** um achado de revisão tem **contexto de execução**. Antes de
virar regra no plano, a pergunta é "quem vai executar isto?" — quem roda o critério é o
runner, dentro do worktree. A regra geral já está escrita na P04, que é a task que
existe exatamente para isso.

## D-24 — Rodada única por sprint é obrigação do motor, não do wrapper

**Achado (28/09, na primeira rodada de verdade):** a rodada morreu com
`TransicaoInvalida: P04: BLOCKED -> DONE não permitido`, com a P04 **aprovada** (27
testes, revisão APPROVE). Duas causas, ambas de motor:

1. **Rodada dupla.** Um vigia de cron disparou uma SEGUNDA rodada às 15:21 enquanto a
   primeira estava dentro da revisão da P04 (que leva ~5 min). O `writer_lock` só era
   escrito no início da tentativa e **não era renovado durante a revisão** — de fora, a
   rodada parecia morta. A segunda rodada integrou a P01, viu a P04 com o contador no
   limite e a **bloqueou no meio da revisão dela**. Quando a revisão da primeira rodada
   voltou APPROVE, o motor tentou `BLOCKED -> DONE` e caiu.
2. **Bloqueio por limite sem olhar quem está vivo:** `_invocar_agente` comparava o
   contador sem checar se já existia tentativa viva da task.

**Decisão:**

- tabela `run_lock` (sprint_id, pid, iniciado_em, heartbeat): `rodar()` só prossegue se
  `adquirir_run_lock` autorizar; rodada com **pid vivo** é recusada com motivo claro
  ("já existe rodada viva no sprint X (pid N)") e a rodada nova sai sem tocar em nada.
  Lock de processo **morto** é assumido como órfão (crash não pode travar o sprint para
  sempre) e `liberar_run_lock` só derruba o lock de quem é dono;
- **batimento de vida** (thread a cada 20 s) renovando `run_lock` **e** o `writer_lock`
  das tentativas do sprint — revisão longa deixa de parecer rodada morta para qualquer
  observador (tela, guarda, vigia);
- a checagem de limite **não bloqueia** task com tentativa viva;
- `TRANSICOES["BLOCKED"]` passa a aceitar `DONE`: bloqueio por limite é decisão sobre
  tentativas, não sobre o trabalho — se a revisão da última tentativa aprova depois, a
  entrega está validada e precisa poder ser finalizada.

**Recuperação dos dados:** P04 finalizada `BLOCKED -> DONE` pela transição nova (dado
real exercitando a correção); P01 voltou a DONE porque foi devolvida a RETRY por falha
de portão do motor (ver D-25), não por trabalho — o commit `00fc3a4` e a revisão
APPROVE estão preservados.

## D-25 — O portão de segurança julga o DIFF, não a árvore inteira

**Achado:** a P01 foi integrada (`d39b632`), os portões `build`, `testes` e `lint`
passaram, e só `seguranca` falhou — por um achado em
`.autodev/sprints/DEVFACTORY-002/decisions.md:78`, arquivo que **já estava na main** e
não tem relação nenhuma com a P01. Era a documentação da decisão que consertou
exatamente esse mesmo falso positivo na sprint 2: o texto citava um literal com forma de
chave de API. (O valor só aparecia truncado na leitura de terminal, o que me levou a
achar que o portão estava errado — **o portão acertou**; o arquivo é que não devia ter
aquele literal escrito.)

**Consequência prática:** enquanto esse achado existisse, **toda** integração deste
sprint seria rejeitada, task após task, e a P01 seria requeimada indefinidamente por um
problema de documentação de outro sprint.

**Decisão:**

- `_portao_seguranca(cwd, arquivos_mudados=...)`: achado em arquivo **do diff** bloqueia;
  achado em arquivo **de fora do diff** é listado como `PRE-EXISTENTES (nao bloqueiam;
  exigem HAQ)` no detalhe do portão e não reprova a integração;
- `Integrador.arquivos_do_merge(wt, commit)`: o recorte vem do diff do merge contra o 1º
  pai — exatamente o que a task integrada mudou;
- sem `arquivos_mudados` (chamada direta/CLI), a varredura da árvore continua valendo;
- a doc da D-22 foi reescrita para descrever o literal sem escrevê-lo.

**Pendência de política (para o autor):** hoje um segredo em arquivo antigo **avisa e
não bloqueia**. Se a preferência for bloquear, é uma linha em `policies.yaml`
(`integracao.bloquear_pre_existentes: true`).

## Testes

`.autodev/tests/test_rodada_unica_e_portao.py` (8 testes): rodada dupla recusada com pid
vivo, lock órfão assumido, liberação só pelo dono, batimento renovando os dois locks,
`BLOCKED -> DONE` legal, segredo pré-existente não bloqueando, segredo no diff
bloqueando, e varredura completa quando não há recorte. Suíte do motor: **202 verdes**.

> **Nota de merge (28/09, Hermes):** este arquivo tem DOIS registros, com numeração
> própria que colide — o de cima é o do sprint (D-23 comando dos critérios, D-24 rodada
> única, D-25 portão de segurança) e o de baixo foi escrito DENTRO das tasks da 004
> (D-24, D-25, D-26, D-27, com outros significados). Os dois foram preservados como
> escritos, na ordem em que cada lado os produziu: **D-24 e D-25 aparecem duas vezes de
> propósito**. Não renumerar — a numeração é parte do registro das tasks.

## Registro das tasks da 004 (numeração das próprias tasks)

## D-24 — 2026-09-28 — P01 não pode integrar a base da sprint 2

A P01 foi criada sobre `main` (`2ca6ce2`), que não contém o subcomando `plan`,
`autodev/planner.py` nem os testes da sprint 2; eles existem em
`sprint/DEVFACTORY-002/integration`. Integrar essa branch nesta task alteraria 193
arquivos e extrapolaria o escopo de reorganização do README. A seção Planejador já
foi introduzida pela tentativa anterior e permanece com o texto de referência; a
P01 fortalece apenas a prova da estrutura e da preservação integral dos textos.

Não houve remoção nem enfraquecimento de teste de task anterior: o teste P01
anterior foi substituído por verificações estritamente mais fortes (árvore inteira
e igualdade do corpo integral com fixtures). A contagem da suíte sobe de 194 para
197; o arquivo da task mantém 3 casos, com verificações estritamente mais fortes.

## D-27 — 2026-09-28 — P03 remove afirmações instáveis do README

O README deixa de afirmar que o planejador consome cota do agente configurado.
O teste lê por AST a assinatura e o corpo reais de `cmd_plan` em `autodev/cli.py`:
a frase só pode existir quando o valor entregue a `planner.planejar` deriva de um
parâmetro `agent`/`agente` (inclusive `args.agent`/`args.agente`). Um parâmetro
decorativo não libera a frase se o call site continuar passando o literal
`"codex"`. Assim, a regra cobre esta base (sem `cmd_plan`) e a implementação
conhecida na qual o agente do planejador é fixo em `codex`.

A contagem fixa foi substituída pelo comando
`python3 -m pytest .autodev/tests/ -q`, contextualizado como comando resolvido pelo
runner para o interpretador do projeto. Para o terminal, o README indica
explicitamente `.venv/bin/python -m pytest .autodev/tests/ -q`. O teste guarda a
regra mais ampla: em `Estado atual`, o comando do runner deve existir e nenhuma
contagem numérica seguida de `teste(s)`, `test(s)`, `passed`, `verdes` ou `nós` é
permitida. Por isso a contagem histórica da aceitação ponta a ponta também virou
“todos os testes”.

A fixture `readme_estrutura/planejador.md` foi ajustada somente para remover a
frase obsoleta que ela espelhava. A partir desta decisão, essa fixture é um espelho
deliberado da seção do README e qualquer alteração futura exige decisão datada; o
teste P01 ainda compara o corpo integral e nenhum caso foi removido ou
enfraquecido. O caso herdado de P02 mantém as verificações de agente, revisão
humana e segurança e continua rejeitando a frase incorreta. A base `6a09d82`
coletava 224 nós; esta entrega coleta e executa 231, todos passando, portanto não
houve queda de cobertura por casos. A saída integral está em
`evidence/P03-suite-completa.txt`.

## D-25 — 2026-09-28 — P01 fixa a árvore completa do README

A revisão da tentativa 8 demonstrou que validar apenas a janela entre `## Como
rodar` e `## Planejador` não protegia o restante da hierarquia: rebaixar `## Estado
atual` ou `## Segurança` para nível 3 ainda passava. O teste de parentesco foi
fortalecido, sem criar ou remover casos, para comparar a sequência completa e
literal de pares `(nível, texto)`. Assim, qualquer seção colocada sob assunto
incorreto falha, inclusive fora da janela reorganizada. O helper de corpos agora
ancora o título no início da linha e exige ocorrência única; os dois títulos que
delimitam a janela também são explicitamente únicos.

A base histórica continua sendo a documentada em D-24. A fixture de `Como rodar`
reproduz o corpo de `main` (`2ca6ce2`), antes da inserção defeituosa; a fixture de
`Planejador` reproduz somente o bloco introduzido pela P10 em
`sprint/DEVFACTORY-002/P10-codex` (`785988d`), até antes das subseções que vazaram
para dentro dele. A preservação é provada contra esses snapshots históricos, não
contra texto derivado pelo teste.

## D-26 — 2026-09-28 — P02 isola a seção Planejador até o próximo título de nível igual ou superior

O helper `_secao_planejador()` deixou de procurar somente `\n## ` e agora encerra
no primeiro `\n# ` ou `\n## `. A asserção de isolamento foi trocada porque o corte
anterior incluía as subseções `### Ciclo de vida do sprint` e `### Conclusão por
evidência`, fazendo o teste aceitar texto de outro assunto como se fosse conteúdo
do Planejador.

A prova de preservação da suíte foi registrada antes/depois: o arquivo histórico
da P10 tinha 4 casos coletados; esta versão mantém esses 4 e adiciona 1 caso no
próprio `.autodev/tests/test_plan_docs.py`, totalizando 5 casos coletados. A
contagem não diminuiu (4 → 5). O novo caso injeta uma subseção `### Ciclo de vida
do sprint` no texto lido e falha com a implementação antiga, pois exige que esse
texto não apareça na seção retornada.
