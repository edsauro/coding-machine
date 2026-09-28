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
