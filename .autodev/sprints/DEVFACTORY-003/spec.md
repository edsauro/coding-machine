# DEVFACTORY-003 — Portao de qualidade do plano e aprovacao humana

## Objetivo

Transformar as duas licoes que a sprint 2 produziu **na marra** em produto:

1. **D-16 (causa-raiz):** o DAG da sprint 2 mandava 8 das 10 tasks gravarem os
   testes no MESMO arquivo e 6 editarem o mesmo modulo. Cada task que obedecia aos
   proprios criterios apagava o trabalho da anterior; o revisor reprovava por
   regressao (corretamente); a task queimava as 5 tentativas e bloqueava. Nove
   tasks bloqueadas, nenhuma por falta de capacidade do modelo.
2. **D-17:** agente que responde com pergunta de design em modo headless nao
   entregava nada, a suite pre-existente continuava verde e o motor gastava
   revisao para descobrir o obvio.

O que **hoje so existe na cabeca do autor** (e por isso falhou) passa a ser
verificado por codigo, antes de qualquer agente rodar:

- colisao de arquivo entre tasks da mesma onda = **erro**, nao surpresa na
  integracao;
- mesmo arquivo de teste em duas tasks = **erro** (a armadilha D-16);
- criterio sem arquivo ou comando concreto = **erro**;
- editar modulo entregue por outra task sem criterio de preservacao = **aviso**.

E fecha a limitacao que a propria sprint 2 documenta na P10 — *"o plano gerado
sempre precisa de revisao humana antes de rodar"* — deixando de ser recomendacao e
passando a ser **portao**: o sprint so roda com aprovacao registrada (quem, quando
e o hash do `dag.json`). Alterar o DAG depois de aprovado invalida a aprovacao.

## Por que esta sprint agora

- A sprint 2 entrega o planejador (`autodev plan`): a partir dela o DAG passa a ser
  **gerado por agente**. Sem o portao de qualidade, o mesmo defeito de contrato que
  parou a sprint 2 sera gerado automaticamente, em escala, em toda sprint futura.
  O verificador precisa existir **antes** de o planejador virar rotina.
- O custo do defeito ja esta medido: ~2h30 de maquina, 76 reenfileiramentos
  forcados e 9 tasks bloqueadas na noite de 27/09.

## Escopo

Entra:

- checagem de colisao de arquivo (mesma onda e arquivo de teste) no plano e no
  validador do motor;
- relatorio de impacto do plano (`autodev prever`): arquivos por onda, colisoes,
  tasks sem arquivo nomeado;
- portao de aprovacao humana (`autodev aprovar`), com hash do `dag.json`;
- aceitacao ponta a ponta com driver falso (sem gastar cota);
- documentacao e checklist derivado do D-16.

Nao entra:

- alterar a escada de modelos ou a cadeia de revisores;
- merge em `main` (politica do motor: nunca automatico);
- chamar agente real na aceitacao;
- substituir o `verificar_plano.py` por codigo do motor: o script fica como
  ferramenta do autor (somente leitura), o motor ganha a sua propria checagem.

## Ondas

```
onda 0: P01                      (colisoes_de_arquivo em planner.py)
onda 1: P02, P03                 (guarda no motor | modulo de impacto) — arquivos disjuntos
onda 2: P04                      (comando prever na CLI)
onda 3: P05                      (portao de aprovacao: CLI + orquestrador)
onda 4: P06                      (aceitacao ponta a ponta)
onda 5: P07                      (documentacao + checklist)
```

Serializada de proposito: depois do que aconteceu na sprint 2, task que mexe em
arquivo de outra task espera a vez. O ganho de paralelismo nao paga o risco de
conflito de merge.

## Checklist de plano (vira criterio de aceitacao na P07)

- [ ] cada task escreve em **arquivo de teste proprio**, que nenhuma outra task cita;
- [ ] nenhuma colisao de arquivo entre tasks da **mesma onda**;
- [ ] ao editar modulo entregue por task anterior: criterio explicito de
      **preservacao** ("estender sem remover");
- [ ] todo criterio cita **arquivo ou comando concreto**;
- [ ] task que estende modulo de outra **depende** dela (direta ou transitivamente).

Verificacao: `.venv/bin/python .autodev/scripts/verificar_plano.py DEVFACTORY-003`

## Dependencia

Depende da sprint `DEVFACTORY-002` estar integrada em
`sprint/DEVFACTORY-002/integration` (o planejador: `autodev/planner.py`,
`plan_prompt.py`, `validar_e_ordenar`, `escrever_sprint`). Esta sprint **nao
comeca** antes disso; o plano esta escrito agora para ser revisado pelo autor
enquanto a sprint 2 roda.

## Riscos

| Risco | Tratamento |
|---|---|
| Sprint 2 mudar a assinatura de `planner.py` (P04–P09 em andamento) | P01 declara os simbolos que preserva; se a assinatura mudar, a P01 da sprint 3 e replanejada antes de rodar |
| Aprovacao por hash travar demais o uso | O hash e so do `dag.json`, nao do repositorio; regerar/aprovar e um comando |
| `autodev prever` virar duplicata do `verificar_plano.py` | O script e ferramenta do autor (fora do motor); a P03/P04 entregam a versao do motor, com teste |

## Alternativas consideradas (e descartadas)

1. **So documentar o checklist no README.** Barato, mas foi exatamente o que a
   sprint 2 fez na P10 e nao impediu nada: recomendacao nao bloqueia geracao.
2. **Fazer o revisor LLM reprovar colisoes.** Ja acontece — e caro e reativo:
   gasta uma revisao de 5 min por tentativa para descobrir um defeito que o
   validador ve em milissegundos, sem gastar cota.
3. **Deixar o planejador gerar e o autor revisar a mao.** E o estado atual
   (limitacao documentada na P10). Vira gargalo justamente quando o volume cresce,
   que e o motivo de existir o planejador.

## Convenção do comando de teste

Os critérios e o campo `teste` usam **`python3 -m pytest ...`** de propósito: é a forma
que o **runner do motor** resolve — ele executa o comando com `cwd` dentro do worktree e
o sandbox mapeia o interpretador do projeto. Escrever `.venv/bin/python -m pytest` nos
critérios **quebra o portão de testes**, porque dentro do worktree o venv do projeto não
existe de forma útil (aconteceu em 28/09 na DEVFACTORY-004: 12 tentativas perdidas com
`No module named pytest` — decisão D-23). Para rodar a suíte **à mão**, fora do motor, o
README manda `.venv/bin/python -m pytest`.
