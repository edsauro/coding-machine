import re
from pathlib import Path


README = Path(__file__).parents[2] / "README.md"


def _titulos():
    return [
        (len(match.group("marcador")), match.group("texto"))
        for match in re.finditer(
            r"^(?P<marcador>#{1,6}) (?P<texto>.+)$",
            README.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]


def test_como_rodar_recupera_subsecoes_antes_do_proximo_titulo_de_nivel_dois():
    titulos = _titulos()
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


def test_planejador_e_secao_irma_de_como_rodar_e_precede_os_agentes():
    titulos = _titulos()

    assert titulos.index((2, "Como rodar")) < titulos.index((2, "Planejador"))
    assert titulos.index((2, "Planejador")) < titulos.index((2, "Os agentes"))


def test_conteudo_das_secoes_reorganizadas_continua_presente():
    texto = README.read_text(encoding="utf-8")
    for trecho in (
        "### Acompanhar ao vivo (tela de eventos)",
        "## Planejador",
        "### Ciclo de vida do sprint",
        "### Conclusão por evidência",
        "`.autodev/sprints/DEVFACTORY-NNN/`",
        "`plan` somente prepara os arquivos locais do sprint",
    ):
        assert trecho in texto
