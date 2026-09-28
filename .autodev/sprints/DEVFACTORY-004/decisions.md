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
