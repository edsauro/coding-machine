"""Orientações canônicas para o agente que transforma uma spec em DAG."""

PROMPT_PLANEJAMENTO = """
Ao preencher os critérios e o campo 'teste' de cada task, use exatamente
`python3 -m pytest ...`. Não use `.venv/bin/python` nem outro caminho de venv
relativo: o worktree nao tem venv; o runner do motor resolve o python3 para o
interpretador do projeto.
""".strip()

