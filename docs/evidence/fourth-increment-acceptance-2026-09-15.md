# Evidencia do quarto incremento — 2026-09-15

## Suite rapida

A suite local completa, incluindo regressao dos incrementos 1, 2 e 3, concluiu com 72 testes
antes do aceite real. O workflow do GitHub Actions executa `ruff check .` e a mesma suite no
Windows/Python 3.12.

## ZIP B3 real opt-in

Fonte: `10-09-2026_NEGOCIOSAVISTA_DRV.zip`, SHA-256
`fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f`. O ZIP tem
50.762.669 bytes e contem TXT de 529.161.058 bytes.

- Linhas lidas: 7.277.116; instrumentos encontrados: 474; rejeicoes estruturais: 0.
- Acoes globais: 7.277.113 novas e 3 cancelamentos.
- Selecao WINV26: 6.262.194 eventos novos/negocios validos; 0 cancelamentos; 0 rejeicoes.
- Intervalo WINV26: 2026-09-10 09:03:00.560 ate 18:31:27.232 UTC-03
  (`12:03:00.560Z` ate `21:31:27.232Z`).
- Candles: 1m=563, 2m=283, 5m=114, 15m=39, sem preenchimento artificial.

A fonte completa reportou 242 precos nao positivos em instrumentos de spreads/taxas que nao
eram WINV26. O intervalo global da fonte alcanca 2026-09-11, enquanto a selecao WINV26 permanece
uma unica `TradingSession` de `DataNegocio=2026-09-10`. Nenhum desses fatos exigiu inferencia ou
alteracao silenciosa: o contrato selecionado ficou isolado e nao teve linhas rejeitadas ou
canceladas.

## Backtest de validacao

Estrategia manual: EMA(9), VWAP real, volume relativo(20) e rolling high(5) anterior; BUY,
target/stop de 100 pontos, custo fixo 0,5 ponto por lado e slippage fixo 1 ponto por lado.

- Trades: 264; wins: 136; losses: 128; breakeven: 0.
- Saidas: 136 TAKE_PROFIT e 128 STOP_LOSS; nenhuma posicao aberta/overnight.
- Gross P&L: 1.480 pontos; slippage: 528 pontos; custos: 264 pontos; net P&L: 688 pontos.
- Identidade comprovada: `1480 - 528 - 264 = 688` pontos.
- Max drawdown: 1.549 pontos; max consecutive losses: 8; win rate: `17/33`.

Estes numeros sao validacao mecanica do pipeline, nao avaliacao/recomendacao da estrategia.

## Determinismo e cache

As duas execucoes produziram:

- run_id: `sha256:b6c827a1bb44cd91a622cbc7465a2078471d3e8272ab3c388ee3605fd98d4d9a`;
- market_dataset_id: `sha256:f89f98d43b3338f54e31270d175c54aecb5f26ee237182d313d0bb97d0d4b2cc`;
- feature_set_id: `sha256:93a14a84860ef6d39c4a6285d7a46d96c80cdb4ccfb0d79b474235d8ef5e27a2`;
- window_fingerprint: `sha256:f96b8d20433743662a757b52f432f5a889bc2b247afcd4a7970354af32ff5e40`.

Todos os oito artefatos de pesquisa foram byte a byte identicos. A segunda execucao registrou
somente `CACHE_HIT` nas camadas de mercado e features.

| Artefato | SHA-256 |
|---|---|
| feature-registry.json | `3e16b4917e3464773ad86d4d5c42c290d2fa28c5859a0f6b200985f7d70f7ca7` |
| feature-set-manifest.json | `043cf7ea495d25fd8b4545516f026b8765809419f64c40403a9ec14508f7abd3` |
| ledger.jsonl | `27f91d61d2a8a5c2852f5b4de7cd2995ad6e8e7ca382f4afbe18b866e62bbb73` |
| market-dataset-manifest.json | `f867aead8b321cae68fe3a5a14a71ec3f7444a23029207175ff8ad9df995b6be` |
| metrics.json | `174e8164ec14e4a8425840f6a1abe904ef0076132b18a482e2e82678e90c4f03` |
| run-manifest.json | `55d33b2b34b85b7d95ec4dc52de54affdc6376038be3c0498c4446febf4bbbda` |
| signal-journal.jsonl | `02d449f99c31617661d681107094fe359c342af3e596fb54154c331d47793206` |
| strategy-definition.json | `b1bb8ad517076af99cdb55e7423defe59cca41db71cd825e85aad715014af293` |

O resultado completo local esta em `artifacts/fourth-increment-real-20260915/` e nao faz parte do
Git por conter dados B3 grandes/cache.
