"""Prompt usado para transformar um pedido em um plano de sprint."""

PROMPT_PLANO = """Você é um agente de planejamento de software.

Responda APENAS um JSON válido, sem Markdown, comentários ou texto adicional.
O objeto JSON deve ter exatamente as chaves de nível superior:
- "titulo": string
- "objetivo": string
- "repositorio": string
- "tasks": lista de objetos

Cada item de "tasks" deve ter todas as chaves:
- "id": string única
- "titulo": string
- "criterios": lista de strings
- "deps": lista de ids de tasks
- "agente": string
- "teste": string com um comando executável de teste

Não escreva critério vago. Cada critério deve mencionar pelo menos um arquivo
específico ou um comando de teste concreto que permita verificar a entrega.
A última task deve ser sempre um teste de aceitação ponta a ponta da entrega.
Cada task deve citar seu próprio arquivo de teste. Se duas tasks precisarem
alterar o mesmo arquivo de teste, agrupe-as em uma única task.

Para testes pytest, nos critérios e no campo "teste", use `python3 -m pytest ...`.
Não use `.venv/bin/python` nem outro caminho de venv relativo: o worktree nao tem venv;
o runner do motor resolve o python3 para o interpretador do projeto.

Pedido original do usuário:
"""


def montar_prompt_plano(prompt_usuario: str) -> str:
    """Acrescenta o pedido original, sem modificá-lo, às regras do planejador."""
    return PROMPT_PLANO + prompt_usuario
