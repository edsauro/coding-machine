# DEVFACTORY-002 — Planejador: do prompt ao plano executável

## O que esta sprint entrega

Um comando novo, `autodev plan "<pedido em texto livre>"`, que produz um sprint
executável em disco (`spec.md` + `dag.json` + `sprint.yaml`), validado pelas
mesmas regras que o `autodev` já aplica na carga do DAG.

Hoje esse passo é manual: alguém escreve o `spec.md` e o `dag.json` à mão antes
de o orquestrador fazer qualquer coisa. Esta sprint fecha essa metade.

## CONTRATO DE INTERFACES (não invente outros nomes)

Estas assinaturas são usadas por tasks de ondas diferentes. Se você precisa de
algo que não está aqui, **use o que está aqui**; não crie uma variante.

```
autodev/planner.py
  @dataclass Plano:      titulo, objetivo, repositorio, tasks, prompt_original
  @dataclass TaskPlano:  id, titulo, criterios, deps, agente, teste
  validar_plano(plano) -> list[str]            # vazio = válido
  parsear_plano(texto) -> Plano
  validar_e_ordenar(plano) -> list[list[str]]  # ondas; reusa ordem_topologica
  escrever_sprint(raiz, plano) -> Path
  planejar(raiz, prompt_usuario, agente) -> Plano
  exceções: PlanoInvalido, SprintJaExiste, CotaEsgotada
  constante: PALAVRAS_VAGAS

autodev/plan_prompt.py
  PROMPT_PLANO: str
  montar_prompt_plano(prompt_usuario) -> str
```

## REGRAS DURAS PARA O AGENTE

1. **Trabalhe SOMENTE no seu worktree.** Nunca faça `cd` para fora dele, nunca
   toque no repositório principal, nunca faça merge nem push.
2. **Rode os testes antes de terminar:**
   `python3 -m pytest .autodev/tests/ -q`
   A suíte inteira tem que ficar verde. `python3` aqui é resolvido pelo runner
   para o interpretador do projeto — use exatamente esta forma.
3. **Os testes novos vão nos arquivos já indicados nos seus critérios.** Não
   crie um arquivo de teste novo se o critério nomeia um existente.
4. **Não invente APIs do projeto.** Os módulos existentes e seus nomes reais:
   `agents.invocar`, `config.ordem_topologica`, `state.StateStore`,
   `testrunner.rodar`, `sandbox.rodar`, `worktree.WorktreeManager`,
   `planner` (novo). Confira a assinatura no arquivo antes de chamar.
5. **Não altere o comportamento existente** para fazer o seu teste passar. Se um
   teste antigo falhar, o problema é o seu código.
6. **Cada critério é verificável.** Se você não consegue provar um critério com
   um teste ou com um comando, o critério não foi atendido.
7. **Sem dados pessoais.** Nada de nomes, caminhos de HOME ou credenciais em
   código, teste ou documentação.

## Por que o critério importa tanto

O `criterios` de cada task do `dag.json` **vira o prompt do agente**. Um critério
vago produz entrega vaga que passa nos testes — porque o teste verifica o
critério, não a intenção de quem pediu. Por isso o planejador recusa critério
vago com palavra como "melhorar" ou "otimizar" que não cite arquivo nem comando.

## Não-objetivos desta sprint

- Não fazer merge na `main` nem push. O trabalho fica no branch do sprint.
- Não automatizar a decisão de rodar o sprint planejado — quem decide é humano.
- Não publicar, empacotar nem implantar nada.
- Não chamar o planejador dentro do orquestrador: são passos separados.

## Ambiente

- Arch Linux, Python 3.11.16 no venv do projeto (`.venv`).
- Sandbox `bwrap` sem daemon e sem root; o venv é montado só-leitura.
- Agentes: `codex` implementa, `agy` revisa. Quem implementa não revisa.
