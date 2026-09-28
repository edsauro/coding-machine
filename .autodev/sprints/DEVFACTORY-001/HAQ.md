# HAQ — Human Action Queue · DEVFACTORY-001

> Itens que exigem humano/privilegio. Cada um lista o comando exato a executar e
> como o Hermes verifica a conclusao. Enquanto isto esta aberto, **somente as
> tasks dependentes ficam bloqueadas** — o resto do Sprint continua.

## HAQ-001
- **Task:** (sprint)
- **Reason:** O sandbox.py cria um HOME proprio e COPIA ~/.codex/auth.json para dentro de .autodev/sandbox-home/ para o Codex autenticar. Esse arquivo contem um token OAuth vivo dentro do diretorio do projeto.
- **Risk:** alto
- **Dependency:** execucao de qualquer task com o agente Codex
- **Exact human action:**
  ```bash
  # decidir se a copia continua. Alternativas: (a) manter, com o .gitignore + barreira de commit ja implementados; (b) montar ~/.codex/auth.json somente-leitura no sandbox, sem copia; (c) usar um HOME com credencial de escopo reduzido.
  ```
- **Expected result:** Decisao do autor (2026-09-27): montar ~/.codex/auth.json como SOMENTE LEITURA no HOME efemero, sem copia. Implementado: preparar_home() deixa um ponto de montagem vazio e montar_cmd() faz --ro-bind-try do auth.json real; _limpar_copias_antigas() apagou as 11 copias do token que existiam no disco do projeto e dos worktrees.
- **How Hermes verifies completion:**
  ```bash
  # nenhuma copia com conteudo no projeto:
find ~/Code/Coding_Machine -name auth.json -size +0c | wc -l   # -> 0
# a credencial chega ao sandbox somente leitura (ro-bind presente):
.venv/bin/python -c "from autodev import sandbox;print('ro-bind-try' in ' '.join(sandbox.montar_cmd(sandbox.SandboxSpec(worktree='/tmp'))))"   # -> True
  ```
- **Status:** DONE

## HAQ-002
- **Task:** (sprint)
- **Reason:** T01-T14 foram concluidas por evidencia RETROATIVA (rota b): o codigo foi escrito fora do laco do orquestrador. A tentativa esta marcada com origem='retroativo' para nao ser confundida com execucao real.
- **Risk:** medio
- **Dependency:** fechamento do sprint e merge de qualquer branch
- **Exact human action:**
  ```bash
  # revisar o SPRINT-REPORT.md e o origem='retroativo' no banco, e autorizar (ou nao) o encerramento do sprint.
  ```
- **Expected result:** Sprint encerrado com o metodo de conclusao aceito de forma explicita.
- **How Hermes verifies completion:**
  ```bash
  python3 -m autodev status   # deve mostrar done 15 e a decisao registrada em decisions.md D-13
  ```
- **Status:** OPEN

