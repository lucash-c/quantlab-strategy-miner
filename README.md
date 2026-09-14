# QuantLab Scalp Strategy Miner

Base independente e deterministica para pesquisa quantitativa de estrategias de scalp.

O primeiro incremento implementa somente o seguinte fluxo:

```text
CSV canonico v1
  -> normalizacao e Parquet
  -> candles de 1 minuto
  -> indicador SMA versionado
  -> estrategia manual formal
  -> backtest cronologico sobre ticks
  -> ledger, metricas e prova de determinismo
```

Candidate Generator, Strategy Score, LLM, interface, Monte Carlo, mineracao e ranking estao deliberadamente fora deste incremento.

## Principios

- Nenhum preco canonico usa ponto flutuante.
- Todo timestamp de entrada possui offset explicito e e normalizado para UTC.
- Negocios empatados no timestamp sao ordenados por `source_sequence`.
- O arquivo bruto nunca e alterado.
- Candles sem negocios nao sao criados.
- Sinais `ON_CLOSE` so podem executar em eventos posteriores ao candle.
- Stops e targets intrabar sao avaliados usando a ordem dos ticks.
- Artefatos JSON usam serializacao canonica; Parquet possui hash de bytes e hash semantico.

Os contratos completos estao em [docs/contracts/canonical-csv-v1.md](docs/contracts/canonical-csv-v1.md) e [docs/architecture/first-increment.md](docs/architecture/first-increment.md).

## Desenvolvimento

O projeto usa Python 3.12 e esta organizado como um workspace `uv` com pacotes independentes.

```powershell
uv sync --all-packages
uv run python scripts/run_tests.py
```

O comando do slice sera disponibilizado como `quantlab-miner`.

