# Decisões — DEVFACTORY-004

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

## D-26 — 2026-09-28 — P03 remove afirmações instáveis do README

O README deixa de afirmar que o planejador consome cota do agente configurado.
O teste lê por AST a assinatura real de `cmd_plan` em `autodev/cli.py` e proíbe a
frase enquanto a função não existir ou não aceitar um parâmetro explícito de
agente. Assim, a regra cobre esta base (sem `cmd_plan`) e também a implementação
conhecida em que o planejador fixa o agente em `codex`.

A contagem fixa foi substituída pelo comando
`python3 -m pytest .autodev/tests/ -q`, contextualizado como comando resolvido pelo
runner para o interpretador do projeto. O teste agora guarda a regra: em `Estado
atual`, o comando deve existir e nenhum padrão `N testes passando` é permitido.

A fixture `readme_estrutura/planejador.md` foi ajustada somente para remover a
frase obsoleta que ela espelhava; como o teste P01 compara o corpo integral, todo o
restante do oráculo histórico permaneceu idêntico e sua força foi preservada. O
caso herdado de P02 mantém as verificações de agente, revisão humana e segurança,
e evolui a asserção de cota para rejeitar a frase incorreta. Nenhum caso anterior
foi removido. Medição registrada na revisão: base `6a09d82`, 224 nós / 224
passando; esta entrega coleta 231 nós, todos passando, sem queda de cobertura por
casos.

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
