import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
README = ROOT / "README.md"
REFERENCIAS = ROOT / ".autodev" / "fixtures" / "readme_estrutura"
TITULOS_ESPERADOS = [
    (1, "Coding Machine"),
    (2, 'Por que não é só "chamar o Codex num loop"'),
    (2, "Estrutura"),
    (2, "Como rodar"),
    (3, "Acompanhar ao vivo (tela de eventos)"),
    (3, "Ciclo de vida do sprint"),
    (3, "Conclusão por evidência"),
    (2, "Planejador"),
    (2, "Os agentes"),
    (2, "Estado atual"),
    (2, "Portão do plano"),
    (2, "Segurança"),
]


def _texto() -> str:
    return README.read_text(encoding="utf-8")


def _titulos():
    return [
        (len(match.group("marcador")), match.group("texto"))
        for match in re.finditer(
            r"^(?P<marcador>#{1,6}) (?P<texto>.+)$",
            _texto(),
            re.MULTILINE,
        )
    ]


def _corpo_da_secao(titulo: str) -> str:
    texto = _texto()
    secoes = list(re.finditer(rf"^## {re.escape(titulo)}$", texto, re.MULTILINE))
    assert len(secoes) == 1, f"esperada uma seção '## {titulo}', obtidas {len(secoes)}"
    inicio = secoes[0].start()
    proximo = texto.find("\n## ", inicio + 1)
    return texto[inicio : None if proximo == -1 else proximo].rstrip() + "\n"


def test_como_rodar_contem_suas_subsecoes_antes_da_proxima_secao():
    titulos = _titulos()
    assert titulos.count((2, "Como rodar")) == 1
    assert titulos.count((2, "Planejador")) == 1
    inicio = titulos.index((2, "Como rodar"))
    proximo_nivel_dois = next(
        indice
        for indice in range(inicio + 1, len(titulos))
        if titulos[indice][0] == 2
    )

    assert titulos[inicio + 1 : proximo_nivel_dois] == [
        (3, "Acompanhar ao vivo (tela de eventos)"),
        (3, "Ciclo de vida do sprint"),
        (3, "Conclusão por evidência"),
    ]
    assert titulos[proximo_nivel_dois] == (2, "Planejador")


def test_arvore_completa_de_titulos_preserva_niveis_ordem_e_parentesco():
    assert _titulos() == TITULOS_ESPERADOS


def test_corpos_das_secoes_reorganizadas_preservam_o_texto_de_referencia():
    for titulo, arquivo in (
        ("Como rodar", "como_rodar.md"),
        ("Planejador", "planejador.md"),
    ):
        assert _corpo_da_secao(titulo) == (REFERENCIAS / arquivo).read_text(
            encoding="utf-8"
        )
