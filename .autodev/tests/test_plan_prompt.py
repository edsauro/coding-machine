from autodev.plan_prompt import PROMPT_PLANO, montar_prompt_plano


def test_prompt_plano_define_contrato_json_completo():
    prompt = montar_prompt_plano("Crie uma funcionalidade")

    assert isinstance(PROMPT_PLANO, str)
    assert "APENAS um JSON" in prompt
    for chave in ("titulo", "objetivo", "repositorio", "tasks"):
        assert chave in prompt
    for campo in ("id", "titulo", "criterios", "deps", "agente", "teste"):
        assert campo in prompt


def test_prompt_plano_exige_criterios_concretos_e_teste_ponta_a_ponta():
    prompt = montar_prompt_plano("Planeje a entrega")

    assert "critério vago" in prompt
    assert "arquivo" in prompt
    assert "comando de teste concreto" in prompt
    assert "última task" in prompt
    assert "teste de aceitação ponta a ponta" in prompt


def test_prompt_plano_preserva_prompt_usuario_literalmente():
    prompt_usuario = "  Implemente café ☕\nsem alterar ESTA linha.  "

    prompt = montar_prompt_plano(prompt_usuario)

    assert prompt_usuario in prompt


def test_prompt_evitar_colisao_de_arquivos_de_teste():
    prompt = montar_prompt_plano("Planeje")

    assert "arquivo de teste" in prompt
    assert "não podem citar o mesmo arquivo de teste" in prompt
