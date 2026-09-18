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

Esse fluxo descreve o primeiro slice. Os incrementos seguintes estão descritos abaixo;
Strategy Score, LLM, interface, Monte Carlo e ranking continuam fora do escopo implementado.

O segundo incremento acrescenta somente o adaptador do arquivo oficial B3 Negocio a Negocio -
Listados, perfil `_DRV`. Para a amostra de 10/09/2026, `WINV26` e uma selecao explicita de
validacao e nao uma regra de contrato vigente ou rollover.

O terceiro incremento acrescenta historico multi-pregao content-addressed: sessoes formais por
`DataNegocio`, janela configuravel (19 por padrao), candles `1m/2m/5m/15m`, reset de indicadores
por sessao, backtest sem overnight e cache incremental deterministico. Ativo logico `WIN` e
contrato fisico continuam explicitamente separados.

O quarto incremento acrescenta o Feature Engine v2 com numeros racionais canonicos, EMA/ATR
fixed9, VWAP real dos negocios, features de tendencia, volatilidade, volume, estrutura, candle,
momentum e contexto; Strategy Definition v3; custos/slippage determinísticos; ledger v3 e
metricas v3 com valores indefinidos tipados. Ele ainda nao gera estrategias automaticamente.

## Principios

- Nenhum preco canonico usa ponto flutuante.
- Todo timestamp de entrada possui offset explicito e e normalizado para UTC.
- Negocios empatados no timestamp sao ordenados por `source_sequence`.
- O arquivo bruto nunca e alterado.
- Candles sem negocios nao sao criados.
- Sinais `ON_CLOSE` so podem executar em eventos posteriores ao candle.
- Stops e targets intrabar sao avaliados usando a ordem dos ticks.
- Entradas historicas exigem um evento negociavel posterior ao evento de fill.
- Candles finais parciais mantem limites nominais; features posteriores ao ultimo negocio sao
  marcadas como nao executaveis.
- Artefatos JSON usam serializacao canonica; Parquet possui hash de bytes e hash semantico.

Os contratos completos estao em [docs/contracts/canonical-csv-v1.md](docs/contracts/canonical-csv-v1.md),
[docs/contracts/manual-strategy-v1.md](docs/contracts/manual-strategy-v1.md) e
[docs/contracts/b3-listed-trades-drv-v1.md](docs/contracts/b3-listed-trades-drv-v1.md). As
fronteiras dos slices estao em [docs/architecture/first-increment.md](docs/architecture/first-increment.md)
e [docs/architecture/second-increment.md](docs/architecture/second-increment.md).
O terceiro incremento esta em
[docs/architecture/third-increment.md](docs/architecture/third-increment.md), com contratos em
[docs/contracts/trading-session-v1.md](docs/contracts/trading-session-v1.md),
[docs/contracts/historical-dataset-v1.md](docs/contracts/historical-dataset-v1.md) e
[docs/contracts/manual-strategy-v2.md](docs/contracts/manual-strategy-v2.md).
O quarto incremento esta descrito em
[docs/architecture/fourth-increment.md](docs/architecture/fourth-increment.md), com contratos em
[docs/contracts/feature-engine-v2.md](docs/contracts/feature-engine-v2.md) e
[docs/contracts/manual-strategy-v3.md](docs/contracts/manual-strategy-v3.md).

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

Para executar o historico, crie um catalogo `historical-sources/v1` com as fontes e contratos
fisicos explicitamente escolhidos e use:

```powershell
uv run quantlab-miner run-history `
  --catalog caminho\historical-sources.json `
  --strategy caminho\strategy-v2.json `
  --cache artifacts\historical-cache `
  --output artifacts\historical-run
```

O cache e persistente e pode ser reutilizado entre janelas; o diretorio de saida de cada run deve
ser novo.

Para executar o vocabulario quantitativo e o backtest v3:

```powershell
uv run quantlab-miner run-features `
  --catalog caminho\historical-sources.json `
  --strategy examples\strategies\win-features-validation-v3.json `
  --cache artifacts\feature-cache `
  --output artifacts\feature-run
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

A regressao real opt-in do terceiro incremento reutiliza esse ZIP como um unico pregão e valida
os quatro timeframes e duas execucoes equivalentes:

```powershell
uv run python scripts\run_historical_b3_regression.py `
  --input C:\caminho\10-09-2026_NEGOCIOSAVISTA_DRV.zip `
  --output artifacts\third-increment-real-regression
```

O aceite com 19 pregoes reais permanece pendente ate que os arquivos correspondentes sejam
fornecidos.

A regressao real opt-in do quarto incremento executa duas vezes o pipeline de features e compara
os artefatos de pesquisa byte a byte:

```powershell
uv run python scripts\run_feature_b3_regression.py `
  --input C:\caminho\10-09-2026_NEGOCIOSAVISTA_DRV.zip `
  --output artifacts\fourth-increment-real-regression
```

## Candidate Generator v1

O quinto incremento gera todos os candidatos de um espaço explícito e limitado, sem score,
ranking ou filtro por performance. Consulte [arquitetura e identidades](docs/architecture/candidate-generator-v1.md).

Preflight da fixture com 160 candidatos, sem acessar dados de mercado:

```powershell
uv run quantlab-miner plan-mining --search-space examples/mining/fixture-160.json --policy examples/mining/policy-160.json
```

Batch e resume (reutilize o mesmo checkpoint; output deve ser um diretório novo/inexistente):

```powershell
uv run quantlab-miner run-mining --search-space examples/mining/b3-six.json --policy examples/mining/policy-six.json --evaluation examples/mining/evaluation.json --catalog caminho/historical-sources.json --cache artifacts/mining-cache --checkpoint artifacts/mining-state.sqlite --output artifacts/mining-batch
```

Aceites persistentes separados da suíte comum/CI:

```powershell
uv run python scripts/acceptance_mining_fixture.py --work artifacts/mining-fixture-acceptance
uv run python scripts/acceptance_mining_real.py --zip C:/caminho/10-09-2026_NEGOCIOSAVISTA_DRV.zip --work artifacts/mining-real-acceptance
```

Ambos comparam clean, cache aquecido e interrupção/resume byte a byte. O segundo mede seis
backtests completos de WINV26. Budget é limite de geração, não promessa de velocidade.
O ZIP grande não é versionado nem executado na suíte rápida.

## Discovery + Validation v1

O sexto incremento usa holdout cronológico de sessões inteiras, com política inicial 13/6,
gates explicitamente configurados, Discovery Freeze validado antes de qualquer avaliação
Validation e cache de CandidateSessionEvaluation independente de Discovery/Validation.
Não há score/ranking ou aprovação automática para operação real.
Consulte a [arquitetura](docs/architecture/sixth-increment.md) e o
[contrato normativo](docs/contracts/research-v1.md).

O histórico de entrada é um `market-historical-dataset-manifest/v1` previamente preparado com
os caches locais correspondentes. `plan-research` só lê metadata; não ingere/rebaixa o holdout
ou consulta payload de mercado. Gates são obrigatórios em ambos os comandos. Os exemplos sem
critérios significam exatamente PASS auditado como `NO_CRITERIA_CONFIGURED`, não uma regra
de performance ou recomendação de uso em produção.

```powershell
uv run quantlab-miner plan-research --search-space examples/mining/b3-six.json --policy examples/mining/policy-six.json --evaluation examples/mining/evaluation.json --history caminho/market-manifest.json --split-policy examples/research/split-13-6.json --discovery-gate exemplos-do-usuario/discovery-gate.json --validation-gate exemplos-do-usuario/validation-gate.json
uv run quantlab-miner run-research --search-space examples/mining/b3-six.json --policy examples/mining/policy-six.json --evaluation examples/mining/evaluation.json --history caminho/market-manifest.json --discovery-gate exemplos-do-usuario/discovery-gate.json --validation-gate exemplos-do-usuario/validation-gate.json --cache artifacts/research-cache --checkpoint artifacts/research.sqlite --output artifacts/research-run
```

Resume: mesmo checkpoint/cache/configuração e output ainda inexistente. Experimentos concluídos
não são sobrescritos. Um novo protocolo exige checkpoint/output novos. `--previous-experiment`
registra lineage e overlap de holdout como provenance, não altera resultados quantitativos.

Aceites locais opt-in, separados do CI e sem download externo:

```powershell
uv run python scripts/acceptance_research_fixture.py --work artifacts/research-fixture-acceptance
uv run python scripts/acceptance_research_real.py --input C:/caminho/10-09-2026_NEGOCIOSAVISTA_DRV.zip --cache artifacts/mining-real-acceptance/cache --baseline artifacts/mining-real-acceptance/clean-final --output artifacts/research-real-acceptance
```

A fixture gera 20 sessões sintéticas e candidatos nos quatro timeframes independentes; produz
manifest preparado/configurações/evidência persistente, compara todos os bytes clean/warm/resume
e valida rolling. Seus thresholds NÃO são defaults de produção.
No aceite real, `--baseline` deve apontar ao export completo do quinto incremento; use
`--session-cache artifacts/research-fresh-cache` para repetir backtests em cache de avaliações
novo sem apagar o cache existente de mercado. O real é regressão de UMA sessão, NÃO OOS 13/6;
o aceite OOS real depende do fornecimento dos pregões correspondentes.
