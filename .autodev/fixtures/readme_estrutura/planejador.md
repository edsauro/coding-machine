## Planejador

O planejador transforma um prompt em texto livre em um sprint executável. Ele
envia o pedido a um agente, interpreta e valida o plano devolvido, ordena as
tarefas por dependência e materializa os arquivos que o orquestrador consome:
`spec.md`, `dag.json` e `sprint.yaml`.

Por exemplo:

```bash
.venv/bin/python -m autodev plan "Adicionar autenticação à API"
```

O comando cria um novo diretório `.autodev/sprints/DEVFACTORY-NNN/` no disco,
contendo os três arquivos acima. O plano gerado **sempre precisa de revisão
humana antes de rodar**: a validação estrutural não garante que a interpretação
do pedido ou os critérios de aceitação estejam corretos.

`plan` somente prepara os arquivos locais do sprint; ele **não faz merge nem
push**.
