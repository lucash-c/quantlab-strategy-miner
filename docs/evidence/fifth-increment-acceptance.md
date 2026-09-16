# Quinto incremento — aceite de implementação

Candidate Generator v1 implementado no repositório oficial. Nenhum score, ranking, OOS,
sampling, otimização, LLM, UI, book/agressão, realtime, trailing/partial ou rollover automático.

## Entregas e 23 itens de aceite

| # | Item | Resultado/prova |
|---|---|---|
| 1 | Testes | 105 aprovados; 72 casos originais dos incrementos 1–4 + 33 novos. |
| 2 | CI | GitHub Actions verde no commit dc41d2f; link abaixo. |
| 3 | Search Space | MiningSearchSpace v1; seis famílias, confirmações fechadas, grids explícitos, sem OR/NOT automático. |
| 4 | search_space_id | sha256:4ea9e2010ee324e0bf5929f53228991434dfd830ff21be9311daae7209d159f4 |
| 5 | generation_policy_id | sha256:6c9c7057a182c72b72ea60ca2bf94e2f698ebfa423a9785721bf169f94630c8c |
| 6 | candidate_set_id | sha256:0500b6d2bc90f29f26319cbbd7aaeed2b01f40f32e6de96bbc6fe4c3f5e7f137 |
| 7 | T/R/V/D/U | Fixture: 192/32/160/0/160. Real: 6/0/6/0/6. T=R+D+U. |
| 8 | candidate_ids | Todos os 160 IDs da fixture e os seis IDs/evaluation IDs reais nos JSONs anexos. |
| 9 | Canonicalização | AND flatten/sort/dedup/collapse; aliases/declarations; decimais; LT/LTE invertidos; EQ/NE ordenados; CROSS_BELOW invertido; dimensões verificadas. |
| 10 | Deduplicação | Dois templates com labels distintos: T4/D2/U2, mesmo universo e IDs; nunca por igualdade empírica de trades/sinais/métricas. |
| 11 | Contradições | Bounds incompatíveis, mesmo par GT/LTE, EQ/NE, enums e BETWEEN vazio; sem prova mantém candidato. Fixture R32 exclusivamente short>=long. |
| 12 | Budget | 160 permitido; 159 SEARCH_SPACE_EXCEEDS_BUDGET antes de market/features/backtests (mocks provam zero chamadas). Expansion limit antes de spool. |
| 13 | Zero-trade | 148/160 na fixture; 1/6 no real. Todos BACKTESTED; win_rate:null/NO_TRADES e outras razões tipadas. |
| 14 | Cache compartilhado | Fixture: 380 séries (19 sessões × 4 TF × 5 specs), warm380 hits. Real: oito séries criadas uma vez, warm/resume oito hits. |
| 15 | Batch completo | 160 resultados fixture e seis reais; sequencial; ledger/journal/metrics v3 agregados, sem ranking/filtro de lucro. |
| 16 | Checkpoint/resume | Fixture interrompida após80:80 reusados+80 restantes. Real após3:3 reusados+3 restantes. Transação e rollback do atual; validar todos os digests antes de retomar. |
| 17 | Clean × resume | Três caminhos byte-idênticos; exportação final reconstruída por candidate_id. Teste adicional: SQLite bytes diferentes e ordem inversa, shards10000+2 iguais. |
| 18 | B3 real | ZIP completo, WINV26, 10/09/2026, 6.262.194 trades elegíveis; 563 candles1m; features/AST/backtest v3. |
| 19 | Benchmark seis | Cold1078,51s; materialização805,48s; warm270,89s; warm materialização5,98s; média de backtest warm44,11s. |
| 20 | Projeções | 100≈1h14; 1.000≈12h15; 10.000≈5d2h32. Uma sessão desta amostra, projeção informativa sem garantia. |
| 21 | Regressões | Todos os casos originais passam: matemática EMA/ATR/VWAP/Rational, quatro TF, sparse periods, reset, look-ahead, filtros, último tick, partial não executável, SESSION_END e sem overnight. |
| 22 | Fingerprints | IDs/hashes completos nos JSONs; versões/math/cache/registry nos manifests; fricções só afetam avaliação. SQLite/timers excluídos. |
| 23 | Commits | Sete commits de implementação/testes/documentação publicados, mais o commit final destas evidências; lista abaixo. |

CI da implementação final: [105 testes e lint aprovados](https://github.com/lucash-c/quantlab-strategy-miner/actions/runs/35097677205).

Arquitetura normativa: [Candidate Generator v1](../architecture/candidate-generator-v1.md).
Dados completos: [fixture e 160 IDs](fifth-increment-fixture.json), [B3, seis IDs, métricas e benchmark](fifth-increment-real.json).

## Search Space real e resultados brutos

Uma sessão WINV26, 1m, stop100/target100 pontos, janela09:15 inclusiva até11:30 exclusiva;
FIXED_PER_SIDE1 ponto por lado e FIXED_POINTS1 ponto adverso por fill. São valores explícitos
de validação, NÃO taxas reais ou estimativas automáticas de corretora/B3. Ticks continuam
determinando stop/target; níveis são derivados da entrada ajustada.

Layouts: SMA9+close/VWAP+relative_volume20>1,5; CROSS EMA9/EMA21+relative_volume20>1,5;
breakout dos cinco candles anteriores+relative_volume20>1,5+ATR14>0. Rolling exclui atual.

A tabela está em candidate_id crescente, NÃO em ordem de performance. Valores exatos em pontos;
cálculo/ledger usam inteiros escalados (escala comum1), preservando market/execution separadamente.

| ID abreviado | Layout/direção | Trades | Gross | Slippage | Costs | Net |
|---|---|---:|---:|---:|---:|---:|
| 07d8db999e57 | Breakout SELL | 5 | -295 | 10 | 10 | -315 |
| 15054470b8a5 | Trend close SELL | 5 | -500 | 10 | 10 | -520 |
| 369d14a5fefc | EMA cross BUY | 1 | -100 | 2 | 2 | -104 |
| 6ed3b64ab531 | Trend close BUY | 12 | -585 | 24 | 24 | -633 |
| 8a845e31d4df | EMA cross SELL | 0 | 0 | 0 | 0 | 0 |
| edac089fffcc | Breakout BUY | 10 | -590 | 20 | 20 | -630 |

Todos os 33 trades respeitam gross−slippage−costs=net; resultados negativos e zero-trade
não são removidos. Esses resultados não constituem avaliação OOS nem recomendação financeira.

Fonte imutável: ZIP50.762.669 bytes; TXT529.161.058 bytes; 7.277.116 linhas e474 instrumentos.
WINV26:6.262.194 eventos novos/válidos, zero cancelamentos/rejeições na seleção.
Intervalo UTC:2026-09-10T12:03:00.560000000Z até2026-09-10T21:31:27.232000000Z
(local B3:09:03:00.560 até18:31:27.232, UTC−03). Candles1m:563, sem preenchimento artificial.

Sem divergência nova de layout. Permanecem três cancelamentos e242 preços não positivos
fora de WINV26, registrados no relatório original; seleção não afetada. TipoDoCanal continua
literal opaco de auditoria, sem filtro/ordenação/semântica presumida. Native price scale0;
escala comum1 por parâmetros exatos; conversão multiplicativa, sem arredondar preço de mercado.

## Determinismo e performance

Dataset real:sha256:69261352d8f800de69edc7a2faccc1c717a1f24a4707b461ab757adfa13e3c45
Run final (clean=warm=resume):sha256:9615384cec30dd232839ce4fea9bc3e25dd311bddaff4c1f33c5ee5cfe259e5e

Os três backtests científicos originais são byte-idênticos. Ao finalizar a implementação,
metadados de contrato recarregável/registry foram reexportados do zero a partir dos checkpoints
já completos e verificados. Os três exports finais também são iguais; candidate/evaluation IDs,
Strategy v3, metrics, ledger e journal NÃO mudaram. Nenhum backtest concluído foi repetido nessa
reexportação. O benchmark original mede efetivamente seis backtests em cada caminho, não apenas
um export com result cache.

Cache lógico:409.960.226 bytes. Batch científico final:398.682 bytes; quinze arquivos, não
seis diretórios por candidato. Pico RAM não medido de forma confiável (null com motivo explícito).
Interrupção+resume:285,73s incluindo as duas materializações e os seis backtests; a chamada
final retomada executou só os três restantes. Timers e bytes operacionais não entram em IDs.

Projeções calculadas: warm materialização + N×média warm. Valores brutos:4417,09s /44117,14s /
441117,59s para100/1000/10000. Não extrapolar isso como garantia de19 pregões, outro hardware,
outras estratégias/cargas. Budget é limite de geração, não promessa de velocidade.

## Provas automatizadas identificáveis

- test_mining_generation: contagens, budget/expansion, IDs separados, fricções/metadados excluídos, canonicalização e contradições.
- test_mining_templates: seis famílias/modos, bindings, closures, schemas, parâmetros/perfis inválidos, repetibilidade e aliases.
- test_mining_batch: 160 resultados e janela19, todas as famílias no enginev3, shared cache/invalidação, rollback/corrupção/context e clean/warm/resume.
- test_mining_export: ordem física inversa e shards10.000+2, arquivos iguais apesar de SQLite diferente.
- test_numeric_policy: Rational reduzido, denominador positivo, zero0/1; float/bool recusados até no zero; half-even com sinais/empates.
- Regressões v1–v4: SourceB3, quatro timeframes, EMA/ATR/VWAP, sparse rolling, availability/look-ahead, CROSS e sessões/fill/custos/slippage.

Fricções também canonicalizam−0.000→0 e grafias decimais equivalentes antes da avaliação;
teste prova os mesmos IDs e bytes, sem alterar a matemática dos inputs válidos anteriores.

## Commits publicados

- e9cdf9d — bounded semantic generation e preflight exato.
- 0d770f1 — batch, checkpoint transacional/verificado e exportação canônica.
- 58f8fc5 — CLI, schemas e aceites fixture/B3.
- 6c942fa — cache/invalidação e endurecimento de auditoria.
- a1bcd26 — seis famílias no backtest e ordem/shards independentes.
- 459e9a7 — documentação normativa.
- dc41d2f — RATIONAL exato e fricções canônicas.

O commit final das evidências consta no histórico Git publicado; seu hash e CI são apresentados
na conclusão da tarefa (não embutidos recursivamente neste próprio commit).

19 pregões reais continuam pendentes dos arquivos. Isso não bloqueia o incremento:19 sessões
de fixture e a regressão integral de um pregão real foram executadas. Cache das sessões removidas
e equivalência full/incremental continuam cobertos pelas regressões do terceiro incremento.

