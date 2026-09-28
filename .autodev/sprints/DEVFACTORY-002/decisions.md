# Decisões — DEVFACTORY-002

## 2026-09-28 — P09: rastreabilidade e compatibilidade

O DAG passa a persistir `prompt_original`. A asserção de igualdade do conjunto
de chaves em `test_escreve_sprint_completo_no_proximo_id` foi ampliada somente
com essa chave, exigida por P09. Nenhum teste anterior foi removido, nem suas
demais asserções alteradas. O novo arquivo `test_plan_rastreabilidade.py`
acrescenta 15 casos, incluindo preservação literal na spec, DAG e relatório,
plano sem prompt, DAG ausente, inválido ou ilegível e colisão com extras.

O relatório continua derivado do estado: problemas de leitura do DAG resultam
em `nao informado`. O prompt do DAG prevalece sobre `report-extras.json`.

### Evidência e bloqueio de ambiente

- Antes das alterações, `python3 -m pytest .autodev/tests/ --collect-only -q`
  terminou com código 1: `No module named pytest`.
- Após as alterações, o mesmo comando e
  `python3 -m pytest .autodev/tests/ -q` terminaram com o mesmo erro.
- Portanto, a contagem de coleta antes/depois e a aprovação da suíte **não
  puderam ser comprovadas** neste ambiente. A verificação obrigatória permanece
  pendente no runner com pytest disponível; não se declara a suíte verde.
- Verificação direta com Python exercitou 15 cenários do fluxo real, todos
  aprovados. A análise sintática dos arquivos alterados e `git diff --check`
  passaram. Isso não substitui a execução da suíte ou sua coleta.

Na integração, anexar esta entrada ao histórico existente de decisões, caso
esse arquivo já exista na branch de destino, preservando as entradas anteriores.
