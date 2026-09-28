# DEVFACTORY-004 — Correções apontadas pela revisão retroativa da sprint 2

## Objetivo

Corrigir, **com o mesmo processo** (task → agente → revisão → integração), os defeitos
que a revisão retroativa por LLM encontrou na P10 da sprint 2 e as pendências de
rastreabilidade que apareceram nas duas revisões. Nada foi corrigido à mão no trabalho
já integrado: quem corrige é esta sprint.

Fonte dos achados: `~/.hermes/cache/scratch/revisoes-p09-p10/{P09,P10}-veredito-relido.json`
e o registro D-22 em `.autodev/sprints/DEVFACTORY-002/decisions.md`.

## O que esta sprint entrega

1. **README com hierarquia coerente** (P01) — a seção `## Planejador`, entregue pela P10
   da sprint 2, foi inserida **dentro** de `## Como rodar`. Consequência: `### Ciclo de
   vida do sprint` e `### Conclusão por evidência` passaram a ser subseções do
   Planejador. Conteúdo fica, estrutura volta ao lugar.
2. **Teste de documentação que prova o que anuncia** (P02) — `_secao_planejador()` corta
   a seção no próximo `\n## `, então a "seção Planejador" que o teste examina inclui
   subseções que não são dela. O teste passa a isolar de verdade, com mutação que
   derruba o teste quando o vazamento volta.
3. **README sem afirmação que não bate com o código** (P03) — "consome cota do agente
   configurado" (o agente do planejador é fixo em `codex`) e o número fixo "Suíte do
   orquestrador: 123 testes passando", que já nasceu velho.
4. **Critério com comando que roda nesta máquina** (P04) — os critérios e o campo
   `teste` mandavam `python3 -m pytest`, que **não roda aqui** fora do runner do motor
   (o `python3` do sistema não tem pytest). O verificador de planos passa a avisar, e o
   prompt de planejamento passa a exigir a forma que roda.

## Por que isso é produto, e não faxina

O motor exige que cada critério seja **verificável**. Um critério cujo comando não roda
na máquina do dono é um critério que ninguém consegue conferir — foi exatamente o que
dois revisores apontaram de forma independente. E um teste que prova menos do que
anuncia é a mesma classe de defeito que reprovou a P09 duas vezes na sprint 2: parece
cobertura, não é. As duas correções entram no verificador de planos (P04) para valer
para os próximos DAGs, não só para este.

## Dependências

`DEVFACTORY-004` depende de `DEVFACTORY-002` estar integrada — e está (ENCERRADA,
10/10). Os arquivos tocados (`README.md`, `.autodev/tests/test_plan_docs.py`,
`.autodev/scripts/verificar_plano.py`, `autodev/plan_prompt.py`) existem em `main`.

## Onde o trabalho acontece

Branch de integração `sprint/DEVFACTORY-004/integration`. **Merge em `main` nunca é
automático** — continua sendo decisão do autor, junto com o da sprint 2.

## Convenção de teste (lição desta sprint)

Cada task tem **arquivo de teste próprio** (a lição D-16: 8 das 10 tasks da sprint 2
gravavam no mesmo arquivo e cada uma apagava o trabalho da anterior):

| task | arquivo de teste |
| --- | --- |
| P01 | `.autodev/tests/test_readme_estrutura.py` |
| P02 | `.autodev/tests/test_plan_docs.py` (o próprio arquivo sob correção) |
| P03 | `.autodev/tests/test_readme_afirmacoes.py` |
| P04 | `.autodev/tests/test_plano_comando.py` |

## Ondas

- **Onda 1:** P01 (README) e P04 (verificador + prompt) — arquivos disjuntos.
- **Onda 2:** P02 e P03 — as duas dependem da P01, que fecha a estrutura do README.

## Aprovação

`status: PLANEJADO`. Só roda depois de `autodev aprovar DEVFACTORY-004 --por <nome>`
(hash do `dag.json` registrado), como as demais.
