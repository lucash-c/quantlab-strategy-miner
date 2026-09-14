# QuantLab Scalp Strategy Miner

Base independente e deterministica para pesquisa quantitativa de estrategias de scalp.

O nucleo implementa o seguinte fluxo deterministico:

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

O segundo incremento acrescenta somente o adaptador do arquivo oficial B3 Negocio a Negocio -
Listados, perfil `_DRV`. Para a amostra de 10/09/2026, `WINV26` e uma selecao explicita de
validacao e nao uma regra de contrato vigente ou rollover.

## Principios

- Nenhum preco canonico usa ponto flutuante.
- Todo timestamp de entrada possui offset explicito e e normalizado para UTC.
- Negocios empatados no timestamp sao ordenados por `source_sequence`.
- O arquivo bruto nunca e alterado.
- Candles sem negocios nao sao criados.
- Sinais `ON_CLOSE` so podem executar em eventos posteriores ao candle.
- Stops e targets intrabar sao avaliados usando a ordem dos ticks.
- Artefatos JSON usam serializacao canonica; Parquet possui hash de bytes e hash semantico.

Os contratos completos estao em [docs/contracts/canonical-csv-v1.md](docs/contracts/canonical-csv-v1.md),
[docs/contracts/manual-strategy-v1.md](docs/contracts/manual-strategy-v1.md) e
[docs/contracts/b3-listed-trades-drv-v1.md](docs/contracts/b3-listed-trades-drv-v1.md). As
fronteiras dos slices estao em [docs/architecture/first-increment.md](docs/architecture/first-increment.md)
e [docs/architecture/second-increment.md](docs/architecture/second-increment.md).

## Desenvolvimento

O projeto usa Python 3.12 e esta organizado como um workspace `uv` com pacotes independentes.

```powershell
uv sync --all-packages --locked
uv run python scripts/run_tests.py
```

Para executar o slice, use um diretorio de saida que ainda nao exista:

```powershell
uv run quantlab-miner run `
  --input caminho\ticks.csv `
  --strategy caminho\strategy.json `
  --output artifacts\run-001
```

O comando imprime o `run_id` derivado dos hashes semanticos. Consulte o manifest final no
diretorio de saida para auditar dados, estrategia, engines e artefatos.

Para executar o segundo incremento:

```powershell
uv run quantlab-miner run-b3 `
  --input C:\caminho\10-09-2026_NEGOCIOSAVISTA_DRV.zip `
  --contract WINV26 `
  --strategy examples\strategies\winv26-validation-sma-v1.json `
  --output artifacts\b3-run-001
```

## Testes B3

A suite comum usa apenas fixtures pequenas derivadas da amostra real:

```powershell
uv run python scripts\run_tests.py
```

O ZIP integral nao participa dessa suite. Seu aceite e opt-in, roda o fluxo completo duas vezes
e compara os hashes de todos os artefatos:

```powershell
uv run python scripts\run_b3_full_acceptance.py `
  --input C:\caminho\10-09-2026_NEGOCIOSAVISTA_DRV.zip `
  --output artifacts\b3-full-acceptance
```

O aceite da amostra de 10/09/2026 esta registrado em
[docs/evidence/second-increment-acceptance-2026-09-10.md](docs/evidence/second-increment-acceptance-2026-09-10.md).
