# Oitavo incremento — evidência de aceite

Execução local em 21/09/2026. A evidência científica é sintética: 21 sessões e quatro Strategies
SCORED, uma por timeframe 1m/2m/5m/15m. Os números 13/6, paths, cenários, gates e caps são apenas
fixtures. O ZIP B3 de 10/09/2026 continua regressão técnica de uma sessão, sem alegação OOS ou de
Robustness real.

## Resultado científico

- Candidate Set explícito: 4/4 SCORED.
- Walk-Forward `ROLLING_FIXED`: três folds 13 Discovery + 6 Validation, passo 1, gap 0.
- Validation: 18 slots, 8 sessões únicas, 10 overlaps, fração única 4/9; não independente.
- O Discovery Gate impossível falhou nos três folds e mesmo assim todas as três Validation foram
  avaliadas, provando a semântica diagnóstica sem controle de acesso.
- Monte Carlo: bootstrap, 12 paths × 6 blocos, path set compartilhado pelos candidatos, sem replay
  de mercado; sampler SHA-256 counter/rejection e nearest-rank.
- Sensitivity: uma variante OAT por candidato, nunca promovida e sem mudança de Score/ranking.
- Stress: uma configuração pior por candidato e re-backtest integral.
- Quatro assessments `ROBUSTNESS_PASSED`; Top N solicitado 10 emitiu 4, sem padding.
- `robustness_score` e `robustness_rating` permanecem `null` por contrato.

`ROBUSTNESS_PASSED` nesta fixture não significa live-ready. Monte Carlo apenas reorganiza/reutiliza
blocos observados e não cria pregões reais. Sensitivity não é otimização. Execution Stress não
simula book, fila, impacto de mercado ou liquidez. O aceite real multi-pregão permanece pendente.

## Determinismo, cache e invalidação

Clean construiu 16 resultados de família; warm reutilizou 16/16. Após interrupção controlada em
cinco unidades completas, a etapa final resume reutilizou 5 e construiu 11. Todos os arquivos
científicos de clean/warm/resume são byte-identical.

Clean construiu 148 CSEs e reutilizou 96 chamadas repetidas internas; warm construiu 0 e reutilizou
32. Alterar somente a seed construiu quatro resultados Monte Carlo e reutilizou os 12 resultados
Walk-Forward/Sensitivity/Stress. CSEs, Score v1 e famílias não relacionadas conservaram IDs.
Um teste separado aumenta somente o cap operacional `max_paths` de 100 para 200, mantendo 12 paths
solicitados: muda `workload_policy_id`, mas preserva protocol e todos os IDs científicos de família.

## Oracles unitários normativos

- Sampler: bytes de entrada, digest, inteiro unsigned big-endian, tentativa aceita, tentativa
  rejeitada e índice selecionado fixos.
- Permutation A=+10/B=-5/C=+2: todo path full-length totaliza +7; um ledger interno em A prova
  drawdowns distintos. Bootstrap permite total diferente.
- Bootstrap conhecido A,B,A preserva ordem sintética, timestamps fonte e occurrence IDs distintos.
- Sensitivity rejeita delta zero, rejeita decimal não terminante, preserva dois cenários que usam
  uma única variante quantitativa e marca toda variante como não promovida.
- Stress rejeita multiplier=1, fricção menor e decimal não terminante; cost-only preserva o caminho
  e piora net; BUY e SELL provam slippage mudando target/tick de saída; todas as transições,
  inclusive FAIL→PASS, são registradas sem correção artificial.
- Gate cobre casos A–F e precedência de insuficiência sem short-circuit.
- Cinco seletores de candidato, referência inválida, qualified ranking, diversidade e Top N sem
  promoção/padding possuem testes próprios.

## Identidades e benchmark

Os IDs do Candidate Set, protocol, workload, fold plan, source pool, path set, quatro famílias por
timeframe, assessments, qualified set, shortlist, export e contagens operacionais estão no
[JSON de evidência](eighth-increment-fixture.json).

Benchmark não normativo: clean 22,58 s; warm 1,34 s; etapa final do resume 11,90 s; mudança de seed
1,48 s. Duração e cache não entram nas identidades científicas.

O total final de testes, Ruff, commit da main e CI remoto são registrados na entrega após a main
publicada e verde.

## Regressão B3 real técnica

O ZIP real foi reprocessado em modo opt-in: SHA-256
`fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f`, WINV26, sessão
10/09/2026, 6.262.194 negócios e 563 candles 1m. As seis CSEs produziram respectivamente
5/5/1/12/0/10 trades. Clean/warm foram byte-idênticos (`f2152313…b673`) e ledger, journal e
métricas coincidiram com o quinto incremento. O registro declara `oos=false` e
`real_oos_pending=true`; nenhuma família Robustness foi reivindicada para esse único pregão.
