"""DEVFACTORY — kill switches hierárquicos (spec §26).

STOP_ALL > STOP_PROJECT > STOP_SPRINT > STOP_TASK.

Dois canais, propositalmente redundantes:
  * um arquivo `.autodev/STOP` — parada de emergência que funciona mesmo se o
    banco estiver travado ou o processo estiver em outro diretório;
  * a tabela `killswitch` no state.db — parada granular por projeto/sprint/task.

Antes de QUALQUER ação autônoma o orquestrador consulta os dois. Parar não pode
corromper estado: o kill switch é checado ANTES de agir, nunca no meio de uma
escrita.
"""
from __future__ import annotations

from pathlib import Path

NIVEIS = ["STOP_ALL", "STOP_PROJECT", "STOP_SPRINT", "STOP_TASK"]

# Ações que exigem checagem antes de executar (spec §26)
ACOES = ["invocar_agente", "criar_worktree", "rodar_testes", "integrar",
         "abrir_haq", "commit"]


class ParadoPorKillSwitch(Exception):
    def __init__(self, nivel: str, motivo: str = ""):
        self.nivel = nivel
        self.motivo = motivo
        super().__init__(f"Sprint parado por {nivel}: {motivo}")


def caminho_flag(raiz: str | Path) -> Path:
    return Path(raiz) / ".autodev" / "STOP"


def ler_flag(raiz: str | Path) -> tuple[str, str] | None:
    """Lê o arquivo de parada. Formato: primeira linha = nível, resto = motivo."""
    p = caminho_flag(raiz)
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    if not txt:
        return "STOP_ALL", "(arquivo STOP vazio)"
    nivel = txt[0].strip().upper()
    if nivel not in NIVEIS:
        return "STOP_ALL", txt[0][:200]
    return nivel, " ".join(txt[1:])[:200]


def ativar(raiz: str | Path, nivel: str = "STOP_ALL", motivo: str = "") -> Path:
    """Parada de emergência por arquivo — funciona mesmo com o banco travado."""
    p = caminho_flag(raiz)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{nivel}\n{motivo}\n", encoding="utf-8")
    return p


def desativar(raiz: str | Path) -> None:
    caminho_flag(raiz).unlink(missing_ok=True)


def verificar(store, raiz: str | Path, *, sprint: str | None = None,
              task: str | None = None, acao: str = "") -> None:
    """Levanta ParadoPorKillSwitch se houver parada aplicável. Não retorna nada se ok."""
    flag = ler_flag(raiz)
    if flag:
        nivel, motivo = flag
        if nivel == "STOP_ALL":
            raise ParadoPorKillSwitch(nivel, f"{motivo} (arquivo .autodev/STOP)")
    if store is not None:
        nivel = store.killswitch_ativo(sprint, task,
                                       projeto=Path(raiz).resolve().name)
        if nivel:
            raise ParadoPorKillSwitch(nivel, f"banco de estado (acao={acao})")


def estado(store, raiz: str | Path, sprint: str | None = None) -> dict:
    """Diagnóstico legível do que está ativo."""
    linhas = store.conn.execute("SELECT * FROM killswitch ORDER BY nivel").fetchall()
    return {"arquivo": ler_flag(raiz),
            "banco": {r["nivel"]: {"alvo": r["alvo"], "ativo": bool(r["ativo"]),
                                   "motivo": r["motivo"]} for r in linhas},
            "aplicavel_ao_sprint": store.killswitch_ativo(sprint, None) if store else None}
