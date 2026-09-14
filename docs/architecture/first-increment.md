# Primeiro incremento

## Decisoes aprovadas

- O repositorio oficial e este repositorio Git.
- A entrada inicial e o CSV Canonico v1; formatos reais da B3 serao adaptadores futuros.
- Comportamento intrabar exige ticks/trades ordenados.
- Tauri + React permanece apenas uma opcao provisoria e nao faz parte deste incremento.

## Limites arquiteturais

`quantlab_core` contem apenas tipos de dominio, serializacao canonica, Candle Engine,
Indicator Engine, Strategy Schema e Strategy Evaluator. Ele nao importa UI, banco ou codigo
de aplicacao.

`quantlab_data` e responsavel por ler entradas, preservar proveniencia, normalizar, ordenar e
materializar artefatos. A origem e aberta somente para leitura.

`quantlab_backtest` combina sinais produzidos no fechamento dos candles com o fluxo cronologico
de ticks. Entradas e saidas nunca podem consumir um evento anterior ao instante de disponibilidade
do sinal.

`quantlab_cli` orquestra o slice sem conter matematica quantitativa.

## Artefatos

- `normalized-trades.parquet`
- `dataset-manifest.json`
- `candles-1m.parquet`
- `features-1m.parquet`
- `ledger.jsonl`
- `metrics.json`
- `run-manifest.json`

JSON e JSONL sao gravados em UTF-8, com chaves ordenadas, separadores compactos e final de linha
LF. Parquet usa configuracao fixa; seu manifest registra hash de bytes e hash semantico. Campos
volateis como horario de execucao nao fazem parte dos artefatos deterministas.

## Semantica temporal

- Entrada: ISO-8601 restrito com offset `Z` ou `+/-HH:MM` e ate nove casas fracionarias.
- Representacao interna: nanossegundos inteiros desde Unix Epoch em UTC.
- Ordenacao: `(timestamp_ns_utc, source_sequence)`.
- Candle de 1 minuto: intervalo semiaberto `[open_time, close_time)`.
- Nao ha preenchimento de lacunas.
- Sinal `ON_CLOSE`: disponivel em `close_time`.
- Fill: primeiro tick com `timestamp >= signal_available_at` que nao pertence ao candle do sinal.

## Semantica de preco

O CSV usa texto decimal sem notacao cientifica. O normalizador remove zeros fracionarios nao
significativos, determina a escala decimal minima capaz de representar todo o dataset e armazena
`price_units` como inteiro assinado de 64 bits. Stops e targets tambem sao strings decimais e
precisam ser exatamente representaveis nessa escala.

## Determinismo

O dataset recebe identidade derivada de:

- hash SHA-256 dos bytes da origem;
- versao do contrato;
- versao do normalizador;
- esquema canonico;
- hash semantico das linhas normalizadas.

O resultado recebe identidade derivada dos hashes dos dados, candles, features, estrategia,
ledger, metricas e versoes dos engines.

