## Como rodar

Requisitos: Python 3.11+, `git`, e `bubblewrap` (`bwrap`).

```bash
python3 -m venv .venv && .venv/bin/pip install pytest pyyaml

.venv/bin/python -m autodev detect          # quais agentes estão instalados
.venv/bin/python -m autodev init            # valida o DAG do sprint
.venv/bin/python -m autodev status          # estado das tarefas
.venv/bin/python -m autodev run             # executa o sprint
.venv/bin/python -m autodev report          # gera o relatório
.venv/bin/python -m autodev resume          # retoma após interrupção
.venv/bin/python -m autodev haq             # fila de ação humana
.venv/bin/python -m autodev encerrar        # fecha o sprint (estado terminal)
.venv/bin/python -m autodev evidenciar T03 -e "autodev/state.py + 8 testes"
```

Testes do próprio orquestrador:

```bash
.venv/bin/python -m pytest .autodev/tests/ -q
```

### Acompanhar ao vivo (tela de eventos)

```bash
./tela.sh                  # tela permanente: cabeçalho do sprint + uma linha por evento
./tela.sh --once           # um quadro só (para log ou pipe)
./tela.sh --intervalo 5 --linhas 25 --tudo
```

O cabeçalho traz sprint, estado, quantas tarefas já foram integradas, se há rodada
viva e a fila; abaixo, cada evento do motor em uma linha curta — reinício da
codificação e teste, fim de desenvolvimento, testes do pacote, revisão aprovada ou
reprovada (com revisor e número de achados), integração com o commit, bloqueio, espera
de cota e HAQ que dependem de você. É só leitura (abre o `state.db` em modo `ro`) e sai
com Ctrl-C. No Hyprland/Omarchy, para deixar sempre à mão:

```
bind = SUPER, D, exec, $terminal --title=tela-devfactory -e ~/Code/Coding_Machine/tela.sh
```

### Ciclo de vida do sprint

Tarefas têm 12 estados; o **sprint** tem os seus:

```
PLANEJADO → EM_EXECUCAO → EM_VERIFICACAO → ENCERRADO
                  ↘              ↘
                            ABORTADO
```

`encerrar` **recusa** se houver tarefa fora de estado terminal — encerrar com
trabalho em aberto é justamente o que o HAQ existe para evitar. `--forcar` aborta
um sprint travado e grava o motivo.

A fonte autoritativa é `state.db`. O `status:` do `sprint.yaml` é declaração de
intenção, não fato.

### Conclusão por evidência

Quando o trabalho de uma tarefa foi feito **fora** do laço do orquestrador, ele
não pode ser registrado como se o orquestrador tivesse executado — isso seria
evidência falsa.

```bash
.venv/bin/python -m autodev evidenciar T03 -e "descrição do artefato verificável"
```

O comando percorre o caminho válido de transições e marca a tentativa com
**`origem='retroativo'`**. O relatório mostra essa distinção e alerta quando
existe conclusão que não veio do laço.

---
