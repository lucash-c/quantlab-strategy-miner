# Quarto incremento: vocabulario quantitativo formal

O fluxo implementado e:

`historico B3 -> candles -> cache granular de features -> Strategy Definition v3 -> avaliador de
condicoes -> backtest v3 -> journal/ledger/metricas`.

O materializador de mercado nao cria uma SMA implicita. A estrategia informa o fecho exato de
features requerido. Cada serie por sessao e content-addressed por hash semantico dos candles,
hash dos trades quando necessario, especificacao, dependencias, politica de sessao, politica
matematica, schema e versoes. Assim, mudar EMA nao invalida candles e mudar volume nao invalida
features sem dependencia comum.

O processamento e streaming por sessao e por serie. Rolling extrema usa deque monotona O(1)
amortizado; somas rolling, EMA, ATR e VWAP mantem estado O(period) ou O(1). A janela historica
completa nao e carregada em memoria.

Feature Parquet armazena racionais como numerador/denominador canonicos em texto, alem de estado
de warm-up, disponibilidade, executabilidade e razoes. O feature-set manifest liga os cache keys,
hashes e parametros ao dataset de mercado.

O signal journal persiste snapshots completos somente para condicoes verdadeiras, descartes e
fills rejeitados. Avaliacoes falsas sao reconstruiveis por estrategia, feature artifacts e
fingerprints. Isso limita uso de disco sem perder a explicacao dos eventos relevantes.

O Candidate Generator nao existe neste incremento. O registry fechado e a AST sao apenas o
vocabulário seguro e versionado que ele podera consumir futuramente.
