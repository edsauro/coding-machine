# DEVFACTORY-003 — Decisões

## D-01 — Aprovação humana é um portão obrigatório

**Contexto:** o planejador gera `dag.json` automaticamente, mas a Sprint 2
demonstrou que a validação estrutural não basta para garantir um plano seguro.

**Evidência:** o D-16 da Sprint 2 registrou colisões de arquivos entre tasks,
inclusive várias tasks escrevendo o mesmo arquivo de teste, causando regressões,
reprovações e tasks bloqueadas.

**Decisão:** exigir aprovação humana registrada antes de qualquer sprint rodar.
O registro deve conter quem aprovou, quando e o hash do `dag.json`; qualquer
alteração posterior no DAG invalida a aprovação.

**Consequência:** o fluxo documentado passa a ser planejar, prever, aprovar e
rodar. Um plano sem aprovação válida não pode consumir execução ou cota de
agente.
