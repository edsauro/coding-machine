#!/usr/bin/env bash
# tela.sh — tela permanente de eventos do Coding_Machine (sprint autônoma DEVFACTORY).
#
#   ./tela.sh                     # ao vivo (atualiza a cada 2s); sai com Ctrl-C
#   ./tela.sh --once              # um quadro só (bom para log/pipe)
#   ./tela.sh --intervalo 5 --linhas 25
#   ./tela.sh --sprint DEVFACTORY-002 --tudo
#
# O que ela mostra: o estado do sprint no cabeçalho (integradas, rodada viva/parada,
# espera de cota, fila) e, embaixo, uma linha curta por evento do motor — reinício da
# codificação e teste, fim de desenvolvimento, testes do pacote, revisão aprovada ou
# reprovada, integração, sprint concluída, bloqueios e HAQ que dependem de você.
#
# Para ter sempre à mão no Hyprland/Omarchy (exemplo de bind em ~/.config/hypr/bindings.conf):
#   bind = SUPER, D, exec, $terminal --title=tela-devfactory -e ~/Code/Coding_Machine/tela.sh
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$RAIZ/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"

exec "$PY" "$RAIZ/.autodev/scripts/tela.py" "$@"
