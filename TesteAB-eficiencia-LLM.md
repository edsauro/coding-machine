# Teste A/B de eficiência de LLM — plano

**Objetivo:** comparar modelos no MESMO pacote do backlog, do mesmo commit-base, com o mesmo
teste e o mesmo revisor, medindo **qualidade, custo e tempo** — e com isso decidir a escada
de escalonamento com número, não com impressão.

**Pergunta de decisão:** qual modelo entrega o pacote **aceito** pelo menor custo? E a escada
de 5 degraus se paga, ou um degrau só (barato) resolveria?

**Método:** skill `llm-coding-efficiency` (fases 1–6) + protocolo de `capability-ab-test`.
**Estado:** PLANEJADO — não executado. Falta configurar os braços externos e fixar preços.

---

## 1. Linha de base já medida (28/09/2026)

Fonte: `.autodev/state.db` → `report/relatorio-tentativas.pdf` (8 páginas).

| o quê | número |
| --- | --- |
| chamadas do Codex no período | **92** (mediana 6 por pacote) |
| atribuíveis ao **modelo** | **57 (62,0%)** |
| por **defeito do nosso teste/plano** | **27 (29,3%)** — D-16, D-19, D-23 |
| por **infraestrutura** (cota/crash) | **8 (8,7%)** |
| aprovações por chamada | 1ª: **2** pacotes · 2ª–3ª: 0 · 4ª–5ª: 2 · 6ª–10ª: **8** · 11ª–15ª: 1 |
| degrau mais caro (`astra/low`) | **8 chamadas (8,7%)**, assinou 4 aprovações — todas em pacote que carregava defeito nosso junto |
| tokens por tentativa | **não medidos** no período (a fonte morria num `mktemp`); instrumentado a partir de 29/09 (P-10) |

Leitura que importa para o desenho: **quase 30% das chamadas foram perseguindo defeito nosso**.
Um A/B feito hoje, sem descontar isso, mediria o defeito — foi por isso que os candidatos abaixo
excluem todo pacote dentro de janela de culpa.

## 2. Candidatos (e por que os outros não servem)

**Bons — sem janela de culpa, teste próprio e determinístico, sem dependência de outro pacote:**

| pacote | sprint | chamadas | aprov./reprov./s/aval. | aprovada na | por que serve |
| --- | --- | ---: | --- | --- | --- |
| **P08** | 002 | 4 | 4/0/0 | 4ª (sol/low) | o mais barato e limpo; nenhuma reprovação do revisor |
| **P05** | 002 | 6 | 4/2/0 | 6ª (sol/low) | duas reprovações reais → dá sinal do revisor |
| **P07** | 002 | 6 | 4/1/1 | 6ª (sol/low) | perfil igual ao P05 |

**Descartados:** P02, P03, P09 (sprint 002) e P01, P04 (sprint 004) — todos dentro de janela de
culpa de teste/plano; P01 (002), P02 (004) e P15 (001) resolveram numa única chamada (sem
variância para comparar).

## 3. Desenho do braço

Um **braço** = um modelo fazendo todas as tentativas do pacote (não a escada): é o que isola a
variável. Cada braço × N≥3 repetições.

Fixos em todos os braços: commit-base, arquivo de critérios, comando de teste, política de
aceitação e **o revisor** (mesmo modelo, sem cadeia de fallback — senão o experimento compara
rigidez de revisão, não capacidade de codificação).

Medir por execução: aceito? · chamadas · tokens (total do CLI) · custo · tempo de parede ·
findings do revisor.

## 4. Como rodar um braço (concreto)

1. **Sprint de estudo** com 1 task, copiada do `dag.json` de origem com critérios idênticos:
   `.autodev/sprints/ESTUDO-AB-P08-<braço>-<n>/{dag.json,sprint.yaml}`.
2. **Config do braço** (cópia de `.autodev/config/models.yaml` com a escada fixa):
   ```yaml
   codex:
     ladder:
       - {slug: <modelo-do-braço>, effort: <esforço>, tier: 0}   # todos os degraus no mesmo modelo
   ```
   e a escada de revisão fixa no revisor escolhido.
3. **Rodar:**
   ```bash
   cd ~/Code/Coding_Machine
   AUTODEV_CONFIG=<config-do-braço> .venv/bin/python -m autodev --sprint <sprint-do-estudo> run --rodadas 1
   ```
4. **Colher:** `report/gerar_relatorio_tentativas.py` (chamadas e tokens por pacote) e
   `.venv/bin/python -m autodev --sprint <sprint-do-estudo> status -v` (vereditos).
5. **Registrar** a execução em `~/workspace/s_llm-coding-efficiency/<estudo>/` (dados crus +
   relatório + veredito).

## 5. Braços previstos e o que falta

- Braços internos (já disponíveis na conta): `gpt-5.6-luna/low`, `gpt-5.6-terra/low`,
  `gpt-5.6-sol/low`, `gpt-5.6-sol/medium`, `gpt-6-astra/low`.
- Braços externos: **a configurar pelo autor** (é o gatilho deste plano).
- Candidato nativo pouco explorado: `codex-auto-review` (modelo de revisão automática do Codex)
  — anotado para o estudo do REVISOR, não deste.
- Falta ainda: **tabela de preço por modelo** (tokens do CLI são um total; sem preço não há
  custo), e a escolha do revisor fixo.

## 6. Regra de decisão

1. **Aceite primeiro** — braço que aceita menos perde, mesmo sendo mais barato.
2. Findings do revisor como qualidade: menos reprovações e menos findings = melhor entrega.
3. **Custo** (tokens × preço) desempata; **tempo** desempata depois.
4. Nomear o perdedor e **a condição que inverteria** a decisão.

## 7. Lembrete

Cron `testeab-eficiencia-llm` (semanal, segunda 09:00, entrega no Telegram): cobra os braços
externos e o preço por modelo, e se cala quando o estudo já tiver começado.

## 8. Ressalvas honestas

- Tokens do Codex vêm como **um total** (sem separar entrada/saída) — registrar como total,
  nunca inventar o split.
- Repetições custam dinheiro real: ~2 pacotes × 5 braços × 3 repetições ≈ 30 execuções.
  Orçamento a aprovar antes de começar.
- Variação entre execuções de agente é grande: com N=3 o resultado é **direção**, com N≥5 é
  veredito. Registrar o intervalo, não só a média.
