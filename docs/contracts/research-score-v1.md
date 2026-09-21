# Research Strategy Score v1: contrato normativo

## Limite e elegibilidade

`ResearchStrategyScoreV1` consome exclusivamente um `validation-manifest/v1` completo e os
resultados, gates, comparações, candidatos e estados que ele autentica. O pacote de scoring não
executa adapter B3, normalização de mercado, candles, features, backtest ou
`CandidateSessionEvaluation`.

Somente `DISCOVERY_PASSED + VALIDATION_PASSED`, com ambos os Gate Results em `PASS`, é elegível.
Demais candidatos recebem `score_status=NOT_ELIGIBLE`, score nulo e motivo fechado; não existe
score diagnóstico comparável. Todo candidato do Candidate Set, inclusive `NOT_ELIGIBLE` e
`NOT_SCORABLE`, recebe registro content-addressed e participa de `score_set_id`. Apenas `SCORED`
participa do raw ranking.

As políticas são obrigatórias e separadas: `ResearchScorePolicyV1`,
`ResearchRankingPolicyV1` e `ResearchDiversityPolicyV1`. A CLI não possui política default. Os
arquivos em `examples/scoring` são exemplos explícitos de referência.

## Matemática exata

Toda matemática intermediária é `CanonicalRational`: denominador positivo, MDC reduzido, zero
exclusivamente `0/1` e comparação por inteiros. Política ou evidência com racional não canônico,
float, dimensão incompatível ou count fracionário falha fechada.

Cada normalização declara dimensão de origem e de saída. `risk_unit` é exclusivamente a distância
exata e positiva de `StrategyDefinitionV3.stop_loss`, em pontos. Stop realizado, average loss,
slippage, target e distância média de saída nunca substituem esse denominador.

Seja `L(x;a,b)=clamp((x-a)/(b-a),0,1)`. Seja `I(x;a,b)=1-L(x;a,b)`. Seja:

`D(delta;t,f) = 1` para `delta >= -t`; `0` para `delta <= -f`; e
`(delta+f)/(f-t)` no intervalo, com `f > t >= 0`.

`P(x;m,s,e,M)` vale zero em `x <= m`, cresce linearmente até 1 em `(m,s)`, permanece 1 em
`[s,e]`, cai linearmente em `(e,M)` e vale zero em `x >= M`. Exigir `m < s <= e < M`.

### Componentes positivos

| Componente | Entrada e transformação | Peso máximo |
| --- | --- | ---: |
| Performance | average_trade/risk_unit, `L(-1/10,1/5)` | 12 |
| Performance | net_pnl_per_session/risk_unit, `L(-1,2)` | 12 |
| Performance | win_rate, `L(2/5,3/5)` | 6 |
| Risk | max_drawdown/risk_unit, `I(2,10)` | 14 |
| Risk | max_consecutive_losses, `I(1,5)` | 6 |
| Consistency | positive_session_rate, `L(1/3,2/3)` | 14 |
| Consistency | `(total_sessions-losing_sessions)/total_sessions`, `L(1/2,1)` | 6 |
| Stability | average_trade_delta/risk_unit, `D(1/20,1/2)` | 6 |
| Stability | net_pnl_per_session_delta/risk_unit, `D(1/4,2)` | 6 |
| Stability | win_rate_delta, `D(1/20,1/4)` | 4 |
| Stability | trades_per_session_ratio, `L(1/5,4/5)` | 2 |
| Stability | active_session_rate_delta, `D(1/20,1/4)` | 2 |
| Activity | active_session_rate, `L(1/2,1)` | 4 |
| Activity | trades_per_session, `P(1/2,1,10,30)` | 6 |

Os máximos são Performance 30, Risk 20, Consistency 20, Stability 20 e Activity 10, soma exata
100. Discovery não fornece performance positiva: somente referência de estabilidade. Melhora em
Validation atinge 1, sem bônus além de 1. P&L total, profit factor, payoff e average loss não são
inputs oficiais v1; os três últimos são diagnósticos.

Concentration Penalty é `7*L(profit_share;1/2,1) + 3*L(trade_share;1/2,1)`, máximo 10.
`NO_PROFITABLE_SESSIONS` é `NOT_APPLICABLE`, contribuição zero, nunca `share=0`.

`pre_clamp_score = base_score - concentration_penalty` e
`score_exact_before_quantization = clamp(pre_clamp_score,0,100)`. Somente então aplicar
`ROUND_HALF_EVEN` com `score_scale=10000` unidades por ponto. Persistir exato, units, escala e
ajuste de arredondamento. O exato pré-quantização é auditoria e nunca desempate.

## Undefined e auditoria

Zero trades produz `ZERO_TRADES`. Métrica oficial indefinida produz `NOT_SCORABLE`; todos os
termos continuam avaliados para reunir motivos canônicos fechados, sem short-circuit. Motivos
incluem `REQUIRED_METRIC_UNDEFINED`, `INVALID_RISK_UNIT`, `SOURCE_RESULT_INCONSISTENT` e
`MISSING_COMPARISON_METRIC`, preservando candidato, métrica, origem e motivo original.
`NaN`/infinidades são proibidos. No-loss não dá bônus e não inviabiliza por si só.

Cada termo registra origem, métrica, dimensões, valor exato, normalizador, transformação,
âncoras, peso, valor normalizado, contribuição e status. Metadata de complexidade é apenas
descritiva e não afeta score/desempate.

## Ranking, diversidade e Top N

Raw ranking usa estritamente: score_units DESC; Validation net_pnl_per_session DESC;
Validation max_drawdown ASC; Validation positive_session_rate DESC; candidate_id ASC. Valores
exatos dos desempates ficam na linha. Não há critério oculto.

`semantic_group_id` preserva ativo, timeframe, direção, topologia/posição/papel dos operandos,
operadores, features/versões, categorias, booleans, dimensões e tipo do filtro temporal. Abstrai
valores numéricos de períodos, thresholds, bounds, stop, target e horários exatos, além de labels
e provenance. O algoritmo v1 é `ROUND_ROBIN_SEMANTIC_GROUP`, máximo 2 por grupo. Raw rank nunca
é reatribuído; excluídos permanecem com `DIVERSITY_GROUP_LIMIT`.

`top_n` é obrigatório e não preenche vagas. Alterá-lo não muda score, raw ranking ou diversified
ranking. O título é “Top N Research Strategies” e o artefato avisa que pesquisa não aprova live.

## Identidades e artefatos

`strategy_score_id` depende do candidato, fingerprints Discovery/Validation/comparison/gates,
Score Policy, engine e risk unit; nunca de outros candidatos, rank, diversidade, Top N, máquina,
caminho, timestamp, cache ou duração. IDs separados existem para score set, raw ranking,
diversified ranking, Top N e export. Mudanças de Ranking/Diversity/Top N não invalidam score.

Export atômico e agregado: policies, input manifest, score set, `strategy-scores.parquet`,
`score-components.parquet`, `semantic-groups.parquet`, rankings JSONL, Top N, distribuição fixa,
selection pressure e `score-manifest.json`. Cache SQLite é operacional, single-writer e excluído
das identidades científicas.

Quatro casas operacionais representam determinismo numérico, não certeza estatística. A
Validation atual é curta. Nenhum score desta etapa constitui OOS real de 19 pregões B3.
