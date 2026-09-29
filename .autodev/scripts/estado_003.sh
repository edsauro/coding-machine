#!/usr/bin/env bash
# Estado do sprint DEVFACTORY-003 para o VIGIA (gate do cron, campo `monitor`).
#
# Por que existe: o vigia não pode acordar o agente a cada 30 min por 5 dias. O cron
# compara a saída deste script com a do tick anterior e só roda o agente quando algo
# MUDA. Por isso a saída é DETERMINÍSTICA — sem hora, data, pid ou contador de tempo;
# qualquer coisa dessas faria todo tick parecer diferente e queimaria cota à toa.
set -u
cd /home/saurus/Code/Coding_Machine || exit 1
.venv/bin/python -m autodev --sprint DEVFACTORY-003 status 2>&1 | sed -n '1,4p'
# Tasks realmente INTEGRADAS (estado na fonte da verdade). Contar commits por mensagem
# enganava: qualquer commit que MENCIONA o sprint entrava na conta.
echo "integradas: $(sqlite3 .autodev/state.db "SELECT COUNT(*) FROM tasks WHERE sprint_id='DEVFACTORY-003' AND estado IN ('DONE','INTEGRATED')")"

# ---- status de pacotes para o Telegram do autor (pedido de 29/09) ------------------
# A mensagem do vigia sempre leva "Em andamento: sprint3 P0X, P0Y" e "Finalizado P0Z".
# Determinístico de propósito: o gate compara tick a tick, então é ESTA linha que faz o
# vigia avisar a cada MOVIMENTO de pacote — P03 -> P04 não muda contagem nenhuma.
sp=$(sqlite3 .autodev/state.db "SELECT sprint_id FROM tasks GROUP BY sprint_id ORDER BY MAX(atualizado_em) DESC LIMIT 1")
n=$(printf '%s' "$sp" | grep -o '[0-9]\+$' | sed 's/^0*//')
em_curso=$(sqlite3 .autodev/state.db "SELECT COALESCE(GROUP_CONCAT(task_id, ', '), '') FROM (SELECT task_id FROM tasks WHERE sprint_id='$sp' AND estado IN ('RUNNING','VERIFYING','REVIEW','RETRY','WAITING_HUMAN') ORDER BY task_id)")
[ -z "$em_curso" ] && em_curso=$(sqlite3 .autodev/state.db "SELECT COALESCE(GROUP_CONCAT(task_id, ', '), '-') FROM (SELECT task_id FROM tasks WHERE sprint_id='$sp' AND estado='QUEUED' ORDER BY task_id)")
ultimo=$(sqlite3 .autodev/state.db "SELECT COALESCE(task_id, '-') FROM tasks WHERE sprint_id='$sp' AND estado IN ('DONE','INTEGRATED') ORDER BY atualizado_em DESC LIMIT 1")
echo "pacotes: Em andamento: sprint${n} ${em_curso}, Finalizado ${ultimo}"

# Trapaça que o gate precisa enxergar: o motor TRAVADO não muda de estado, então as
# linhas de cima ficariam idênticas e o agente nunca acordaria. Aqui a saída muda UMA vez
# quando o log passa de 20 min sem crescer (e volta quando volta a andar). Sem a idade no
# texto: idade mudaria a cada tick e acordaria o agente à toa.
log=".autodev/sprints/DEVFACTORY-003/logs/orquestrador.log"
if [ -f "$log" ]; then
  if [ $(( $(date +%s) - $(stat -c %Y "$log") )) -gt 1200 ]; then
    echo "rodada: SEM SINAL (log parado ha mais de 20 min)"
  else
    echo "rodada: viva"
  fi
fi
