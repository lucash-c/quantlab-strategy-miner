# Research v1: contrato normativo

## Inputs

`research-split-policy/v1`: `CHRONOLOGICAL_TAIL_HOLDOUT`, `validation_sessions=6`,
`min_discovery_sessions=13`, `min_validation_sessions=6`, `insufficient_sessions=ERROR`.
Inteiros positivos estritos; validation_sessions não pode ser menor que seu mínimo.
As únicas sessões disponíveis são as presentes no manifest/cache fornecido.

`discovery-gate-policy/v1` e `validation-gate-policy/v1` são obrigatórios, explícitos,
`semantics=all-criteria-undefined-fails/v1`. `criteria=[]` é permitido, mas NÃO um selo de
performance: resultado PASS, informação `NO_CRITERIA_CONFIGURED`.
Não há threshold implícito de produção. Os arquivos `*-no-criteria.json` mostram exclusivamente
a opção explícita sem critérios; o usuário deve configurar seus próprios gates.

Cada critério: `metric`, `operator` entre GT/GTE/LT/LTE/EQ/NE, `threshold` inteiro, decimal
em string ou racional `{numerator: string, denominator: string}`. Não aceitar float, boolean,
formula/código ou métrica fora do registry fechado. Threshold de contagem/delta de contagem
deve ser integral. Frações equivalentes e critérios repetidos produzem a mesma política.
`criterion_id` opcional ao reimportar evidência deve coincidir com a semântica canônica.

O JSON Schema documenta a estrutura; os validators normativos Python também verificam registry,
restrições por estágio, canonicalização matemática e identidades derivadas.

## Gates

Todos os critérios são AND e TODOS são avaliados. Falhas ordenadas por criterion_id.
Valor indefinido SEMPRE falha, inclusive NE; `UNDEFINED_METRIC` preserva o motivo original.
Registrar criterion_id, metric, operator, threshold canônico, observed_value, PASS/FAIL,
reason e undefined_reason. Zero/negativo é backtest quantitativo válido, não erro de execução.

Discovery só usa métricas da própria partição. Validation pode também usar as 12 comparações.
Razões fechadas: MIN_TRADES_NOT_MET, MIN_ACTIVE_SESSIONS_NOT_MET, NET_PNL_NOT_MET,
PROFIT_FACTOR_NOT_MET, MAX_DRAWDOWN_EXCEEDED, VALIDATION_DEGRADATION_EXCEEDED,
THRESHOLD_NOT_MET, UNDEFINED_METRIC, NOT_IN_DISCOVERY_PASS_SET, EMPTY_DISCOVERY_PASS_SET.
Razões especializadas correspondem às orientações de operador; demais comparações usam
THRESHOLD_NOT_MET. Nenhum motivo introduz interpretação não configurada de bom/ruim.

## Métricas

Forma canônica: `{status: DEFINED, value: {numerator: string, denominator: string}, reason: null}`
ou `{status: UNDEFINED, value: null, reason: <code>}`. Zero é exclusivamente 0/1; denominador
positivo e fração reduzida. Não usar NaN/Infinity. Pontos são preço, não reais. Win rate é
fração, não percentagem 0…100. Fricções e classificação wins/losses usam Metrics v3 líquido.

Sejam N todas as sessões da partição; T os trades; P_i net P&L e T_i trades da sessão i:

| Métrica | Fórmula |
| --- | --- |
| total_sessions | N |
| active_sessions | count(T_i > 0) |
| profitable_sessions / losing_sessions / flat_sessions | count(P_i > 0 / < 0 / = 0); flat inclui sessão sem trades |
| trades_per_session | T / N |
| net_pnl_per_session | sum(P_i) / N |
| positive_session_rate | count(P_i > 0) / N |
| active_session_rate | count(T_i > 0) / N |
| best_session_net_pnl / worst_session_net_pnl | max(P_i) / min(P_i), incluindo zero |
| largest_profitable_session_share | max(max(P_i,0)) / sum(max(P_i,0)); sem lucro: NO_PROFITABLE_SESSIONS |
| largest_trade_count_session_share | max(T_i) / T; sem trades: NO_TRADES |

Gross/net/cost/slippage/drawdown são convertidos exatamente de `_units` para pontos.
Metrics v3 existentes permanecem preservadas em `backtest_metrics`; views racionais de pesquisa
ficam em `metrics`. Uma linha para toda sessão, independente de atividade.
Win rate/average trade sem trades: NO_TRADES; average win sem wins: NO_WINS;
average loss/profit factor sem losses: NO_LOSSES; payoff preserva os motivos Metrics v3.

Drawdown = max(high-water-mark da equity realizada - equity realizada), início zero, trades
cronológicos. Máxima streak conta net P&L < 0, atravessa sessões vazias e é resetada por
net P&L >= 0, inclusive breakeven. Não é drawdown intratrade/mark-to-market.

## Comparação

Para trades, trades_per_session, active_sessions, active_session_rate, win_rate, average_trade,
profit_factor, net_pnl_per_session, max_drawdown, max_consecutive_losses:
`<metric>_delta = Validation - Discovery`.

Somente trades_per_session e profit_factor têm `<metric>_ratio = Validation / Discovery`.
Ambos devem existir e Discovery deve ser estritamente positivo. Zero/negativo resulta
`DISCOVERY_DENOMINATOR_NOT_POSITIVE`; fonte indefinida resulta `SOURCE_METRIC_UNDEFINED`,
com lista de motivos originais ordenada Discovery, Validation. Sem P&L retention/score composto.

## Outputs e estados

SplitPlan/Partition têm modelos estritos, ids revalidados e JSON Schemas aditivos.
SessionEvaluation possui input normativo, sessão, escala própria, métricas, ledger/journal
counts, evaluation_id e result_fingerprint. PartitionEvaluation referencia avaliações de
sessão ordenadas, métricas por sessão/globais e hashes de ledger/journal reconstruídos.

Discovery estados conceituais: PENDING -> BACKTESTED -> PASSED ou FAILED_GATE.
Validation: NOT_RUN -> BACKTESTED -> PASSED ou FAILED_GATE. O checkpoint operacional guarda
conclusões de sessão/agregados e o export final guarda os estados finais por candidato.
Não publicar experiment_id parcial; ele só existe após Validation concluída ou pass set
vazio formalmente finalizado. Candidato não aprovado nunca recebe payload Validation.

Artefatos finais: protocolo, split/views, políticas, pass set/freeze, candidatos/provenance,
registry de features/métricas, generation manifest, selection-pressure (contagens sem score),
Parquet de avaliações/gates/comparações, audit JSONL, status, validation-experiment/manifest.
Ordenar por identidade/cronologia, não performance. Snapshot causal das entradas continua
o do journal v3, ligado a signal_id; nenhuma mudança no contrato de audit anterior.

Referências completas de identidade, cache, autorização e publicação:
[arquitetura do sexto incremento](../architecture/sixth-increment.md).
