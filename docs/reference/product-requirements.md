# Scalp Strategy Miner — requisitos de produto

## Objetivo

Sistema independente para descobrir estrategias de scalp a partir de historico de mercado. Nao
opera em tempo real, nao envia ordens e nao integra corretoras ou plataformas operacionais.

O pipeline pretendido e:

```text
Dados -> Normalizacao -> Candles -> Indicadores/Features -> Candidatas -> Backtests
      -> Validacao -> Score -> Ranking -> Registry -> Exportacao
```

## Escopo inicial

- Ativo: familia Mini Indice B3 (WIN), um ativo por vez.
- Timeframes pretendidos: 1m, 2m, 5m e 15m.
- Hardware alvo: Windows, Intel Core i5, 8 GB RAM; nenhuma dependencia de GPU.
- Dados processados em chunks, com Parquet, cache, workers limitados e checkpoints.

## Regras essenciais

- Descoberta quantitativa, deterministica e reproduzivel.
- LLM, quando existir, somente interpreta texto para `MiningConstraints` confirmaveis.
- Nenhum acesso a informacao futura.
- Modos de avaliacao distintos: `ON_CLOSE`, `INTRABAR` e `HYBRID`.
- Intrabar deve respeitar a granularidade efetivamente disponivel.
- Indicadores e formulas possuem versao explicita.
- Nenhuma regra de estrategia e persistida apenas como texto humano.
- Score e politica de validacao sao configuraveis, versionados e auditaveis.

## Validacao futura

Discovery/validation, out-of-sample, walk-forward, Monte Carlo, sensibilidade, slippage, custos,
regimes, estabilidade por periodo e quantidade minima de trades.

## QuantLab Strategy Package

Contrato JSON versionado com extensao `.qlstrategy`, independente do banco do Minerador. Deve
conter AST formal de regras, definicoes/referencias de indicadores, compatibilidade de engines,
metricas historicas, contexto, score e checksum.

## Interface futura

Fluxo: Nova Mineracao -> Dados -> Instrucao -> Resumo -> Progresso -> Resultado -> Estrategias
-> Exportacao. Detalhes internos ficam ocultos na experiencia padrao.

## Primeiro incremento aprovado

Somente CSV canonico, normalizacao, Parquet/manifest, candle de 1 minuto, indicador simples,
estrategia manual formal, backtest cronologico, ledger, metricas e prova de determinismo.

Ficam excluidos Candidate Generator, Strategy Score, LLM, UI, Monte Carlo, mineracao e ranking.

