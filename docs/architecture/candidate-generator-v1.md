# Candidate Generator v1 — quinto incremento

`quantlab-mining` gera um universo finito solicitado manualmente e executa todos os candidatos
válidos. Não há ranking, score, seleção por performance, sampling, LLM ou OOS. Core/data/backtest
não importam mining. Um futuro avaliador compartilhado pode substituir o loop sequencial sem
alterar a identidade de estratégia/espaço. Contratos v1/v2/v3 anteriores permanecem intactos.

## Contratos e identidades

MiningSearchSpaceV1, GenerationPolicyV1 e EvaluationConfigV1 são separados, frozen/strict,
com campos desconhecidos proibidos. JSON floats/NaN/Infinity são recusados. Schemas publicados
descrevem estrutura; registry fechado valida nomes, tipos, perfis e parâmetros antes da expansão.
Grids são listas explícitas, nunca ranges start/stop/step. Confirmações são selecionadas exatamente
na configuração, sem gerar subconjuntos automaticamente.

`H(domain,payload) = "sha256:" + SHA256(canonical_json({"domain":domain,"payload":payload}))`

canonical_json: UTF-8, chaves ordenadas, sem espaços, um LF final.

| Identidade | Payload | Exclusões |
|---|---|---|
| search_space_id | Projeção semântica do universo solicitado | dataset, fricções, labels, caminhos, budget/segurança, relógio |
| generation_policy_id | Todos os limites explícitos/defaults | dataset, tempo, cache |
| candidate_id | Strategy v3 canônica projetada + política matemática | custos/slippage, dataset, labels, strategy_version, generator/canonicalizer version |
| candidate_set_id | candidate_ids únicos em ordem crescente | proveniência e ordem física |
| evaluation_id | candidato + HistoricalDataset + fricções + escala + engines + seus feature artifacts | caminho, duração, cache hit |
| run_id | Manifest final + hashes dos arquivos exportados | SQLite físico e medições operacionais |

Domains: search-space/v1, generation-policy/v1, candidate-semantic/v1, candidate-set/v1,
candidate-evaluation/v1, candidate-batch/v1. A canonicalização tem proveniência
candidate-canonicalization/v1; essa string NÃO integra candidate_id. Um algoritmo novo que
produzir o mesmo payload semântico obterá o mesmo ID. Versão do generator também só é provenance.

O universo normaliza ordem/multiplicidade de grids, templates e confirmações. Thresholds/bounds
e stop/target decimais são canônicos: 1.500/1.5 e -0/0 não criam IDs diferentes. Bindings de direções
não habilitadas não integram o universo. Multiplicidade original continua auditada nas contagens
e proveniência; labels podem mudar proveniência/run_id, mas não a estratégia.

search-space.json preserva o contrato solicitado recarregável; search-space-semantic.json é
a projeção de hash, não uma segunda linguagem executável de estratégia.

## Templates, grids e bindings

As expressões reutilizam EXCLUSIVAMENTE Feature Engine v2/Strategy Definition v3. Pontos são
strings decimais exatas. Períodos contam candles OBSERVADOS da mesma TradingSession.

| Família | BUY | SELL |
|---|---|---|
| trend_close | close > SMA(p)/EMA(p), ou CROSS_ABOVE | < / CROSS_BELOW |
| trend_pair | MA(short) > MA(long), ou CROSS_ABOVE, mesma família MA | < / CROSS_BELOW |
| vwap_context | close > VWAP/cross, ou BETWEEN(close−VWAP, bounds) | < / CROSS_BELOW; bounds SELL explícitos |
| breakout_previous | close > rolling_high(p,false) | close < rolling_low(p,false) |
| momentum | point_change(p) ou n_candle_return(p) > threshold BUY explícito | < threshold SELL explícito |
| candle_context | direction == UP; geometry > threshold; BETWEEN(close_range_position,bounds) | direction == DOWN; geometry conserva >; bounds SELL explícitos |

MA permite apenas sma_close/ema_close. Geometry apenas absolute_body/candle_range: NÃO cria
razão corpo/range. Breakout não gera alias booleano equivalente. Cada átomo seleciona um único
mode; outro modo exige outra entrada. COMPARE/CROSS é grid somente onde listado. Thresholds/bounds
direcionais usam direction_grids.BUY/SELL; nunca presumir negação/inversão de valores.

Confirmações volume:

- RAW: volume > threshold (QUANTITY);
- ROLLING: volume > rolling_mean_volume(p, include_current explícito);
- RELATIVE: relative_volume(p, include_current explícito) > threshold (RATIO).

Confirmações volatility:

- ATR_THRESHOLD: atr_wilder(p) > threshold;
- ATR_RANGE: BETWEEN(atr_wilder(p), bounds), inclusividade explícita;
- ROLLING_RANGE: candle_range > rolling_mean_range(p, include_current explícito).

Volume/volatilidade/geometry usam thresholds não negativos e conservam o sentido em BUY/SELL.
Confirmações VWAP/candle/momentum usam os mesmos modos fechados das famílias.

Perfis autorizados, além de nenhuma confirmação:

- trend_close/trend_pair: volume, VWAP, volatilidade, volume+VWAP, volume+volatilidade;
- VWAP: volume OU volatilidade (escolha de configuração, não AST OR);
- breakout: volume, volatilidade, volume+volatilidade;
- momentum: volume, candle, volume+candle;
- candle: volume OU momentum.

## Expansão, canonicalização e segurança

Defaults: budget=10.000 (máximo=50.000), expansão=1.000.000, 32 valores/grid, 32 templates,
períodos=1…1000; 1…3 átomos AND; profundidade de condição=1 átomo/2 AND; até 6 features no closure;
12 eixos variáveis; um CROSS; um filtro horário. Operandos não aumentam profundidade da condição.
Tudo isso pertence à generation_policy, não ao search_space_id.

T é calculado ANTES da expansão: soma dos produtos de grids ativos por template/direção ×
timeframes × stops × targets × windows. Acima do limite: SEARCH_SPACE_EXPANSION_LIMIT_EXCEEDED,
sem spool de expansão/materialização. Expansão é streaming recursivo, com spool SQLite para
strategies/IDs/proveniência, não todas as Strategy Definitions simultaneamente em RAM.

Constraints: short_period < long_period, breakout include_current=false, complexidade fechada.
Contadores: V=T−R; T=R+D+U. R=mecânicos, D=duplicatas, U=únicos. U acima de budget falha com
SEARCH_SPACE_EXCEEDS_BUDGET, contagens/dimensões, ANTES de importação/features/backtests.
Nunca truncar. U=0 é permitido quando todos os bindings forem rejeitados mecanicamente.

Canonicalização limitada: aliases de features substituídos por IDs do spec/dependências;
declarations dependency/ID-ordered; AND flatten/sort/dedup/collapse; decimais normalizados;
LT→GT invertido, LTE→GTE invertido; EQ/NE operandos ordenados; CROSS_BELOW→CROSS_ABOVE invertido.
Dimensões devem ser compatíveis. Estratégias manuais v1/v2/v3 não são reescritas.

Contradições: relações incompatíveis no mesmo par, bounds/ BETWEENs sem interseção, EQ/NE
incompatíveis, enums exclusivos, comparações constantes falsas. Sem prova local: manter.
Não comparar sinais/trades/métricas para deduplicar ou rejeitar.

## Avaliação, cache e auditoria

Candidate Set → union(feature closures) por timeframe → materializar cada spec uma vez por
TradingSession → backtests sequenciais. Apenas features utilizadas. Cache granular v2 mantém
spec/versões/parâmetros/dependências/candle ou tick fingerprints/política matemática. Thresholds
e fricções não invalidam séries; volume não invalida SMA/candles.

O backtest consome exatamente Strategy v3 completa, com fricções explícitas da avaliação.
Mantém ON_CLOSE, warm-up/reset por sessão, partial candle não executável, horário no sinal/fill,
último tick proibido para entrada e SESSION_END. Não há overnight. Ticks são lidos em lotes
Parquet cronológicos, sem carregar a janela inteira.

Todos os candidatos concluídos, inclusive trades=0, são BACKTESTED. Metrics v3 preserva razões
indefinidas tipadas. Ledger/journal v3 preservam snapshots de sinais verdadeiros/descartados/fills
recusados, envelopados por candidate/evaluation IDs. Falsos são reconstruíveis por Strategy,
features e fingerprints. Proveniência permite agrupar família, timeframe, direção e parâmetros;
complexidade/features são reconstruíveis da própria Strategy.

Armazenamento agregado: candidates.jsonl; candidate-provenance.jsonl (origem/multiplicidade);
feature-index.jsonl; results.parquet (Metrics v3 JSON canônico TEXT, sem conversão float);
audit shards JSONL com até 10.000 records; contratos, registry/versões e manifests. Não há um
diretório por candidato. Guardar cache imutável referenciado por key/hash para reconstrução.
Ordem final é candidate_id, NÃO performance.

## Checkpoint e publicação

SQLite é operacional, WAL + lock de writer do sistema operacional. Backtest, auditoria inteira,
métricas e completed são uma transação por candidato. Exceção/Ctrl-C faz rollback do atual;
anteriores permanecem válidos. --stop-after é hook de aceite após commit, sem mudar IDs/universo.

Resume exige contexto exato: manifest/request/IDs/versões/feature hashes. Quick_check, evaluations,
sequência/contagens e digests de todos os resultados/audit são verificados ANTES de novo backtest.
Corrupção/cache inconsistente/schema impossível/erro matemático/bug abortam o batch; não continuar
com FAILED silencioso. Correção só pode reutilizar checkpoint com identidades/versões/hashes
compatíveis. Nenhum hash científico depende dos bytes físicos do SQLite.

Conclusão RECONSTRÓI exportação do zero: ordenar candidate_id/ordinal e redeterminar shards.
Staging curto para Windows, publicação atômica, recusar sobrescrita. Ordem física de chegada ao
banco não determina bytes finais. Parquet byte-a-byte pressupõe mesma versão/configuração PyArrow.

## Performance e limites

Relatório operacional é sibling da saída, fora do manifest científico: tempo total/materialização/
backtests, hit/reuso e bytes de cache/artefatos. Timers float são permitidos EXCLUSIVAMENTE nessa
medição, nunca em preços/features/P&L. Pico de memória é explicitamente não medido de modo confiável.
Memória mantém IDs/escalas/specs pequenos O(U+features), strategies no spool e ticks streaming.

Budget limita geração, não promete rapidez. Benchmark B3 de seis candidatos inclui cache frio,
warm e interrupção/resume. Projeções 100/1000/10000 são informativas, sem garantia de hardware,
carga, dados ou complexidade. Nenhum paralelismo novo. 19 pregões reais continuam pendentes de
arquivos; fixtures de 20 sessões provam a janela. Não implementar recursos fora do escopo aprovado.
