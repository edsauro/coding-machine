# Sprint DEVFACTORY-001 — Registro de Decisões

Uma decisão por linha do tempo, com o **porquê**. Decisões que vieram de um bug
real encontrado em execução estão marcadas com **[bug]**, e são as mais caras —
cada uma custou uma execução perdida.

---

## D-01 — Estado em SQLite, não em memória

**Contexto:** o orquestrador precisa sobreviver a crash e a exaustão de cota, que
pode durar horas.

**Decisão:** o `StateStore` (SQLite) é a **fonte autoritativa** de tudo. Nada de
estado em memória que não esteja persistido.

**Consequência:** o relatório é derivado do banco, nunca redigido de memória. Um
número no relatório pode ser rastreado até a linha que o produziu.

**Relacionado:** o `status:` do `sprint.yaml` é **declaração de intenção**, não
fato. O fato mora em `sprint_state.estado`.

---

## D-02 — Nenhum merge automático em `main`

**Contexto:** automação com permissão de escrita em git é a forma mais rápida de
destruir um repositório.

**Decisão:** o sprint termina numa branch `sprint/<sprint>/integration`. O merge
para `main` é sempre ato humano.

**Consequência:** o produto do sprint é uma branch revisável, não uma surpresa na
`main`. Nenhuma exceção no Sprint 1.

---

## D-03 — Um worktree por tarefa

**Contexto:** duas tarefas rodando no mesmo diretório colidem em arquivos.

**Decisão:** cada tarefa recebe worktree e branch próprios
(`sprint/<sprint>/<task>-<agente>`), com `base_commit` registrado.

**Consequência:** dá para reconstituir exatamente de que ponto do código cada
tentativa partiu.

---

## D-04 — Sandbox com bubblewrap, sem root e sem daemon

**Contexto:** o agente escreve código; ele não pode alcançar `~/.ssh` nem
`~/.codex/auth.json`.

**Decisão:** `bwrap` com o worktree RW e o sistema RO. O HOME real **não** é
montado — o sandbox tem um HOME próprio. Sem sudo, sem container runtime.

**Consequência:** houve uma decisão de privacidade derivada — ver **D-12**.

---

## D-05 **[bug]** — AGY headless exige `-D`, senão mente que funcionou

**Contexto:** o adaptador do AGY era invocado em modo headless sem a flag de
permissão.

**Sintoma:** **exit 0, stdout vazio**. O orquestrador classificava como sucesso.

**Causa:** sem `-D`, o AGY auto-**nega** toda ferramenta. O agente não falha —
ele simplesmente não faz nada, e devolve sucesso.

**Decisão:** passar `-D` sempre, e tratar **"sucesso silencioso"** (exit 0 +
stdout vazio + stderr reclamando) como **falha classificada**.

**Consequência:** é a decisão mais importante do sprint. Um agente que mente que
trabalhou é pior que um agente que falha — a falha é visível, a mentira não.

---

## D-06 **[bug]** — AGY exige `--print-timeout` com unidade

**Contexto:** o timeout era passado como `--timeout 900`.

**Decisão:** a flag correta é `--print-timeout`, e o valor precisa de unidade:
`900s`. Não `900`.

**Consequência:** os wrappers ganharam `timeout` **e** `timeout_fmt` na
configuração, em vez de string montada à mão no adaptador.

---

## D-07 **[bug]** — `RETRY → RUNNING` não era permitido e matava o sprint

**Contexto:** o laço de retry, ao reexecutar uma tarefa, tentava mover a tarefa
para `RUNNING` vindo de `RETRY`.

**Sintoma:** o sprint morria na 2ª tentativa de qualquer tarefa.

**Causa:** a máquina de estados não declarava `RETRY → RUNNING`, e a tarefa
também não voltava para a fila.

**Decisão:** re-enfileirar a tarefa antes de movê-la para `RUNNING`.

**Consequência:** regressão coberta por teste
(`test_retry_apos_teste_vermelho_reenfileira_e_conclui`). Sem retry funcional, o
sprint inteiro é decorativo.

---

## D-08 — Cota é espera de recurso, não falha

**Contexto:** a conta gratuita do Codex esgota. Isso não é um bug do código.

**Decisão:** exaustão de cota vira `WAITING_RESOURCE` com checkpoint e
**retomada por CONTINUAÇÃO** — o trabalho já feito é preservado. Não consome
tentativa de implementação.

**Consequência:** o delay é configurável. Em produção, 5h10m; em teste, curto —
para que a aceitação não leve horas.

---

## D-09 — Quem implementa nunca revisa

**Contexto:** um agente revisando o próprio código aprova sempre.

**Decisão:** revisão cruzada obrigatória — codex→agy, agy→codex. Quando o
revisor cruzado está indisponível, um fallback determinístico assume, e isso fica
**registrado como tal** no resultado da revisão.

**Consequência:** a revisão por LLM é um portão **a mais**, não o único. Os
portões objetivos de segurança rodam sempre, com ou sem LLM.

---

## D-10 — Escalonamento de modelo tem regra, não intuição

**Contexto:** "subir o modelo" parece sempre a resposta para uma falha.

**Decisão:** escalona um degrau após **2 tentativas materialmente informadas**.
Falhas de ambiente, dependência e permissão **nunca** escalonam modelo. 4ª falha
repetida troca de agente; 5ª bloqueia a tarefa e abre item no HAQ.

**Consequência:** o critério de "materialmente informada" é o fingerprint de
falha (task + classe + assinatura + arquivos + testes falhos). Retry sem
evidência nova não conta.

---

## D-11 — O relatório sai do banco

**Contexto:** relatórios de sprint autônomos são notoriamente otimistas.

**Decisão:** as 16 seções são calculadas a partir do `state.db`. Nenhum número é
escrito à mão.

**Consequência:** quando não há dado, o relatório mostra vazio — não inventa um
resumo plausível.

---

## D-12 — A cópia do HOME no sandbox nunca é versionada

**Contexto:** para o sandbox funcionar, o `sandbox.py` cria um HOME próprio e
**copia** `~/.codex/auth.json` para lá, com `chmod 600`.

**Risco:** esse arquivo contém um token OAuth vivo. É um segredo real dentro do
diretório do projeto.

**Decisão:** `.autodev/sandbox-home/` inteiro entra no `.gitignore`, com o motivo
escrito no próprio arquivo. O commit tem uma barreira que **aborta** se
`auth.json`, `sandbox-home`, `.venv/` ou `state.db` estiverem no stage.

**Consequência:** rodar o sprint e publicar o repositório são compatíveis. Sem
essa decisão, o primeiro `git add -A` publicaria a credencial da conta.

---

## D-13 — T01–T14 concluídas por evidência, não por execução simulada

**Contexto:** o DAG descreve a **construção** do orquestrador. O código foi
escrito durante o desenvolvimento, fora do laço do próprio orquestrador. As 15
tarefas ficaram em `NEW` com zero tentativas.

**Opções:**

- **(a) Executar formalmente** — rodar `autodev run` e deixar o orquestrador
  implementar suas próprias tarefas. **Rejeitada:** o Codex seria chamado para
  implementar código que já existe. É exercício de teatro, não de engenharia, e
  produziria evidência falsa sobre um trabalho que não aconteceu.
- **(b) Concluir por evidência** — registrar cada tarefa como concluída a partir
  do artefato verificável que já existe (módulo + testes que passam).

**Decisão:** **(b)**, escolhida pelo autor do projeto.

**Como foi implementado, para não virar fraude:** o método
`concluir_task_evidenciada` percorre o **caminho válido** de transições
(`PLANNED → QUEUED → RUNNING → VERIFYING → DONE`) em vez de forçar o estado, e
marca a tentativa com **`origem='retroativo'`**. Quem ler o banco depois vê a
diferença entre trabalho executado pelo orquestrador e trabalho registrado
retroativamente.

**Consequência:** a T15 (aceitação autônoma) **não** é retroativa — ela executa
de verdade, porque é justamente ela que prova o loop.

---

## D-14 — O sprint ganhou estado terminal

**Contexto:** descoberto ao tentar responder "quais os próximos passos para
encerrar o sprint". A máquina de 12 estados governa **tarefas**, não o sprint. O
`status: EM_EXECUCAO` do `sprint.yaml` era uma string escrita à mão que **nada
no código atualizava**.

**Decisão:** adicionar o ciclo de vida do sprint ao domínio —
`PLANEJADO → EM_EXECUCAO → EM_VERIFICACAO → ENCERRADO` (mais `ABORTADO`), com
transições validadas, e o comando `autodev encerrar`.

**Consequência:** `encerrar_sprint` **recusa** se houver tarefa fora de estado
terminal. Encerrar um sprint com trabalho em aberto é exatamente o que o HAQ
existe para evitar. `--forcar` aborta um sprint travado e grava o motivo.

---

## D-15 — Migração de schema idempotente e **genérica**

**Contexto:** a coluna `attempts.origem` (D-13) é nova, mas o `SCHEMA` usa
`CREATE TABLE IF NOT EXISTS` — coluna nova **não** aparece em banco já existente.

**Decisão inicial:** `_migrar()` checa a coluna e altera se faltar.

**Decisão revisada [bug]:** a versão coluna-a-coluna era frágil por construção, e
o bug apareceu no banco real: `haq.failure_class` estava declarado no `SCHEMA` mas
ausente no `state.db` de verdade, e só quebrou quando o HAQ foi usado —
`table haq has no column named failure_class`. Os testes passavam porque criam
banco novo.

A migração passou a ser **genérica**: lê as colunas do próprio `SCHEMA` e
reconcilia qualquer uma que falte. Uma segunda lista escrita à mão envelheceria
exatamente como o schema velho que causou o problema.

**Consequência:** coluna nova no `SCHEMA` agora chega sozinha aos bancos
existentes. Coluna de `PRIMARY KEY` faltando não dá para adicionar com
`ALTER TABLE` — isso **levanta erro alto** em vez de seguir com schema torto.

---

## D-16 **[bug]** — `pytest -k` casa também com o nome do módulo

**Contexto:** a evidência da T07 foi registrada rodando `-k sandbox`. O arquivo é
`test_git_sandbox.py`.

**Sintoma:** o comando selecionou os **22 testes do arquivo** em vez dos 4 do
sandbox — nenhum `deselected` apareceu, o que denunciou o problema.

**Causa:** o `-k` do pytest casa contra o nome do teste **e** contra o caminho do
módulo. `sandbox` aparece em `test_git_sandbox.py`.

**Decisão:** seleção de evidência não pode ser uma palavra que apareça no nome do
arquivo. E o registro original da T07 foi **corrigido com evento de auditoria**
(`evidencia_corrigida`), não apagado — o fato registrado era verdadeiro, mas o
campo `selecao` estava impreciso.

**Consequência:** as outras 12 seleções foram conferidas pelos `deselected` e
nenhuma tinha o mesmo vazamento.

---

## D-17 **[bug]** — `python3` do PATH não é o interpretador do projeto

**Contexto:** o `criar_fixture.py` rodava a autoverificação com `["python3", ...]`.

**Sintoma:** `ModuleNotFoundError` — o `python3` do PATH nesta máquina é o venv do
**Hermes**, que não tem `pytest`. O gerador parecia quebrado.

**Decisão:** usar `sys.executable`, que é o interpretador que está de fato
rodando o script.

**Consequência:** vale para qualquer subprocesso que precise de dependência do
projeto — nunca confiar no `python3` do PATH.

---

## D-18 **[bug]** — documentação do fixture estava desatualizada

**Contexto:** o docstring do `criar_fixture.py` afirmava "3 testes; 1 passa e 2
falham".

**Fato:** o fixture tem **8 testes; 4 passam e 4 falham**.

**Decisão:** corrigir o docstring para o número real, medido pela execução.

**Consequência:** um comentário desatualizado sobre o estado inicial de um fixture
de TDD é pior que nenhum — ele descreve o vermelho que não existe mais.

---

## Decisões herdadas (pré-sprint)

| Decisão | Regra |
|---|---|
| Merge em main | **nunca** automático; o trabalho fica no branch do sprint |
| Sandbox | `bwrap`, sem daemon e sem root; o HOME real nunca é montado |
| Revisão | quem implementa não revisa |
| Cota | espera de recurso, não falha; não consome tentativa de implementação |
| Escalonamento | só com evidência nova; erro de ambiente nunca escala modelo |
| Skills de terceiros | auditar antes de instalar |
| Saída de skill | sempre em `~/workspace/s_<skill>/` |
