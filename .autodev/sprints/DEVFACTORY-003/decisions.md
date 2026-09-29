# DEVFACTORY-003 — Decisões

## D-01 — Aprovação humana é um portão obrigatório

**Contexto:** o planejador gera `dag.json` automaticamente, mas a Sprint 2
demonstrou que a validação estrutural não basta para garantir um plano seguro.

**Evidência:** o [D-16 da Sprint 2](../DEVFACTORY-002/decisions.md) registrou
colisões de arquivos entre tasks,
inclusive várias tasks escrevendo o mesmo arquivo de teste, causando regressões,
reprovações e tasks bloqueadas.

**Decisão:** exigir aprovação humana registrada antes de um sprint `PLANEJADO`
rodar. O registro deve conter quem aprovou, quando e o hash do `dag.json`;
qualquer alteração posterior no DAG invalida a aprovação. Sprints iniciados
antes do portão continuam retomáveis sem o registro, para preservar execuções
legadas.

**Consequência:** o fluxo documentado passa a ser planejar, prever, aprovar e
rodar. Um plano em estado `PLANEJADO` sem aprovação válida não pode consumir
execução ou cota de agente.

## D-02 — 2026-09-29 — Contagem medida e preservação do contrato do README

**Contexto:** o critério de P07 pede a contagem de testes em `Estado atual`, mas
o [D-27 da Sprint 4](../DEVFACTORY-004/decisions.md) substituiu números fixos pelo
comando que mede a suíte. Esse contrato é protegido por
`.autodev/tests/test_readme_afirmacoes.py::test_estado_atual_mede_suite_sem_contagem_fixa`.

**Decisão:** neste critério, a contagem é satisfeita pelo comando de medição
`python3 -m pytest .autodev/tests/ -q` no runner (no terminal, usar o interpretador
do ambiente virtual do projeto). O resumo da execução fornece o número vigente;
não reintroduzir uma contagem fixa no README nem enfraquecer o teste do D-27.
Esta é a conciliação explícita do critério de P07 com a decisão posterior.

**Preservação:** manter a árvore de títulos e os textos de referência de
`Como rodar` e `Planejador`, incluindo a seção `Portão do plano` na estrutura
esperada. Verificar conjuntamente `.autodev/tests/test_plan_docs.py`,
`.autodev/tests/test_readme_estrutura.py` e `.autodev/tests/test_readme_afirmacoes.py`.

**Limite do portão:** o checklist do D-16 e a execução de
`.autodev/scripts/verificar_plano.py` são procedimentos manuais do aprovador;
`aprovar` registra a aprovação, mas não chama esse script. E4 verifica arquivos
nomeados por task, não a concretude de cada comando ou de cada critério.
