# Segundo incremento

## Objetivo

```text
B3 Negocio a Negocio - Listados, perfil DRV
  -> selecao explicita de WINV26
  -> eventos new/delete
  -> CSV Canonico v1
  -> pipeline deterministico existente
  -> candles 1m, SMA, estrategia manual, backtest, ledger e metricas
```

`WINV26` e uma escolha exclusiva da amostra de 10/09/2026. Nao existe descoberta automatica
de vencimento, serie continua ou rollover.

Candidate Generator, Strategy Score, mineracao combinatoria, LLM, ranking, Monte Carlo e
interface grafica continuam fora do escopo.

## Fronteiras

`quantlab_data.adapters` conhece o envelope ZIP, o layout B3 DRV, os dominios dos campos e a
projecao para CSV Canonico v1. Ele nao conhece estrategia, SMA ou backtest.

`quantlab_cli.b3_pipeline` executa atomicamente o adaptador e entrega seu CSV ao primeiro
incremento. A partir dessa fronteira, normalizacao, candles, indicador e backtest continuam
usando exatamente os engines existentes.

O fluxo de saida e:

```text
run/
  adapter/
    canonical-trades.csv
    b3-selected-events.parquet
    b3-rejections.jsonl
    b3-import-report.json
  pipeline/
    normalized-trades.parquet
    dataset-manifest.json
    candles-1m.parquet
    features-1m.parquet
    strategy-definition.json
    ledger.jsonl
    metrics.json
    run-manifest.json
  second-increment-run.json
```

## Processamento limitado por memoria

O TXT e lido diretamente do ZIP. A primeira passagem valida as linhas, calcula o hash do TXT,
contabiliza instrumentos e coleta apenas as tombstones do contrato selecionado. A segunda
passagem materializa a auditoria e o CSV, ja conhecendo todas as tombstones. O arquivo integral
e o conjunto de negocios nao sao mantidos em memoria.

O CSV canonico preserva a sequencia da fonte. O normalizador existente usa SQLite temporario
para ordenar os negocios por instante UTC e sequencia antes de gravar Parquet.

## Falhas

- Envelope, nome ou cabecalho divergente: erro de contrato.
- Linha malformada ou dominio desconhecido: rejeicao registrada.
- Qualquer rejeicao: importacao marcada como rejeitada e pipeline quantitativo nao executado.
- Contrato ausente ou sem negocio ativo: pipeline nao executado.
- Saida ja existente: recusada; nao ha sobrescrita.
- Fonte alterada durante a ingestao: erro de contrato.

## Testes

A suite comum usa somente fixtures pequenas, copiadas de linhas reais e acompanhadas por
proveniencia. Ela cobre parsing, preco exato, quantidade, timestamp, ordenacao, sessoes,
cancelamentos e repetibilidade integral.

O teste do ZIP completo e opt-in. Ele exige um caminho explicitamente fornecido, executa o
fluxo completo duas vezes e compara manifests e artefatos. Nao faz parte da descoberta normal
de testes e nao depende de download externo.
