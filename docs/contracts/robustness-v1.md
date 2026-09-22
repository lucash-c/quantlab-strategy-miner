# Robustness v1: contrato normativo

## Fronteira e identidade

Robustness v1 é uma camada derivada de Research Strategy Score v1. Ela não recalcula score,
ranking bruto, `candidate_id`, `strategy_score_id` ou `CandidateSessionEvaluation`. Não existe
Robustness Score/Rating. O fluxo é:

`Score v1 verificado -> Candidate Set -> quatro famílias de fatos -> gate explícito opcional ->
filtro do ranking bruto -> diversidade opcional -> Top N explícito`.

`RobustnessProtocolV1` contém somente decisões científicas. Limites que apenas abortam sem truncar
pertencem a `RobustnessWorkloadPolicyV1`; alterá-los não muda o `robustness_protocol_id` quando o
trabalho solicitado continua idêntico. Cada resultado de família depende somente de sua policy,
fontes, candidato, configuração e versões. O protocol global liga esses resultados no assessment.

## Candidatos e sessões autorizadas

Os seletores fechados são `ALL_SCORED`, `RAW_RANKING_TOP_N`,
`DIVERSIFIED_RANKING_TOP_N`, `TOP_N_OUTPUT` e `EXPLICIT_CANDIDATE_IDS`. Toda referência deve existir
e possuir `score_status=SCORED`; referência inválida é erro. A ordem original é preservada em
`source_ordinal`, enquanto o artefato content-addressed é serializado canonicamente.

O universo cronológico é recebido por `AuthorizedSessionUniverseV1(dataset_id, session_ids)`.
Nenhuma família descobre sessões em diretórios ou caches. IDs ausentes, duplicados, fora do dataset
ou fora da ordem do catálogo falham explicitamente.

## Walk-Forward

`ROLLING_FIXED` usa comprimentos explícitos `D`, `G`, `V`, passo `S` e `min_folds`. Um fold iniciado
em `i` contém Discovery `[i,i+D)`, gap `[i+D,i+D+G)` e Validation
`[i+D+G,i+D+G+V)`. Somente folds completos existem; `DROP_INCOMPLETE_TAIL` registra a cauda.
Histórico com menos de `min_folds` produz `INSUFFICIENT_EVIDENCE`.

A Strategy já está congelada. Discovery e Validation são sempre avaliados em todo fold completo.
Os gates dos dois lados são fatos diagnósticos (`discovery_gate_result`,
`validation_gate_result`, `both_pass`) e nunca bloqueiam a avaliação Validation. O resumo registra
`validation_session_slots`, sessões Validation únicas, slots sobrepostos e a fração única exata;
folds sobrepostos não são apresentados como independentes.

## Monte Carlo por blocos de sessão

Métodos fechados:

- `SESSION_PERMUTATION_WITHOUT_REPLACEMENT`;
- `SESSION_BOOTSTRAP_WITH_REPLACEMENT`.

O path set é gerado uma única vez, independente do candidato. Um path contém apenas índice,
ordinal sintético, sessão/posição fonte e metadados do sampler. A avaliação posterior liga
`candidate_id + path_set_id + fingerprints das CSEs`. Blocos e trades seguem
`path_index -> synthetic_block_ordinal -> local_trade_ordinal`; timestamps fonte são proveniência e
nunca reordenam o path. Repetições recebem occurrence IDs diferentes.

`SHA256_COUNTER_REJECTION_V1` codifica JSON canônico UTF-8 com LF final contendo domain
`quantlab-monte-carlo-sampler/v1`, versão, source pool, método, seed hexadecimal minúscula de 256
bits, path/draw/retry. O digest é inteiro unsigned big-endian. Para limite `n`, aceita-se
`x < floor(2^256/n)*n` e retorna-se `x mod n`; rejeição incrementa somente retry.

Vetor oficial aceito (`source_pool_id=pool`, bootstrap, seed terminada em `1`, path/draw/retry 0,
`n=3`): digest
`656f19858471e7c05cd75b45fa0f8a97cf109c1ec6443c2f1f438f1e1dde6cd8`, inteiro
`45879893874379962795581354991611667277727738711917935092043164423611020635352`,
índice 0. O teste normativo também fixa uma tentativa rejeitada seguida por aceita para
`n=2^255+1`.

Quantis usam `NEAREST_RANK_V1`, sem interpolação. Probabilidade de drawdown exceder threshold usa
estritamente `drawdown > threshold` e persiste `GT`. Permutação full-length conserva o total net e
mede principalmente ordem/equity/drawdown/streak; bootstrap também altera composição e pode mudar
total net. Os paths são trajetórias sintéticas de blocos observados: Monte Carlo não cria pregões
reais, liquidez nova ou observações de mercado independentes.

## Sensitivity

`ONE_AT_A_TIME` aceita feature periods, constantes/limites, stop e target; horário está fora de v1.
As mutações usam `CanonicalRational` por delta inteiro/decimal absoluto ou decimal relativo.
Resultado não decimal finito é `NON_TERMINATING_DECIMAL_RESULT`, sem arredondamento. Delta zero é
erro `NO_OP_PERTURBATION` no preflight.

Cada cenário permanece auditável. Se specs diferentes produzirem a mesma Strategy canônica,
ambos apontam para o mesmo variant `candidate_id` e o trabalho quantitativo é reutilizado. Variante
melhor não é promovida, não entra no Candidate Set e não altera Score/ranking. Sem tolerâncias,
`fraction_within_tolerance` é `NOT_APPLICABLE`.

## Execution Stress

`ABSOLUTE_POINTS` e `MULTIPLIER` resolvem custo e slippage exatos. Multiplicador deve ser `>=1`;
valores resolvidos não podem reduzir a fricção e ao menos uma dimensão deve piorar estritamente.
Configuração igual/menor é `NON_STRESS_SCENARIO`; resultado não decimal finito é inválido, sem
arredondamento.

Todo cenário, inclusive cost-only, reexecuta o Backtest Engine. Não existe ajuste de ledger por
fórmula. Assim slippage pode alterar entrada efetiva, stop/target, tick de saída e trades. As quatro
transições de gate `PASS->PASS`, `PASS->FAIL`, `FAIL->FAIL` e `FAIL->PASS` são fatos permitidos.
Stress v1 não simula book de ofertas, fila, impacto de mercado, latência ou liquidez não observada.

## Assessment, gate e ranking

Sem gate, o status é `ROBUSTNESS_FACTS_ONLY`. Com gate, todos os critérios são avaliados sem
short-circuit. A precedência é: família obrigatória insuficiente ->
`ROBUSTNESS_INSUFFICIENT_EVIDENCE`; senão qualquer falha -> `ROBUSTNESS_FAILED`; senão
`ROBUSTNESS_PASSED`. Família opcional insuficiente não força insuficiência, mas um critério sobre
ela não passa silenciosamente. Família não executada referenciada por critério é erro de preflight.

O qualified ranking filtra o raw ranking original por `ROBUSTNESS_PASSED`, preservando raw rank,
score, scale, grupo semântico, `strategy_score_id` e assessment. Diversidade é aplicada somente se
configurada; Top N é explícito e nunca inclui reprovado para preencher quantidade.
`ROBUSTNESS_PASSED` significa apenas que os critérios explicitamente configurados passaram na
evidência disponível; não significa estratégia live-ready, recomendação ou autorização de trading.

## Cache, checkpoint e publicação

Workload preflight registra folds, paths, blocos amostrados compartilhados, cenários e combinações,
mais estimativas operacionais separadas. Exceder cap falha antes da família e nunca trunca.

O cache de resultado é granular por família; mudar somente seed invalida Monte Carlo e assessment,
não Walk-Forward/Sensitivity/Stress/CSE/Score. SQLite e checkpoint são operacionais e não entram em
IDs científicos. O export final é sempre reconstruído em ordem canônica; clean, warm e resume têm
artefatos científicos byte-identical. Corrupção ou colisão de conteúdo falha fechada.

## Limites de evidência

As configurações 13/6/1/0, paths, cenários, gates e budgets publicadas em examples são fixtures de
aceite, nunca defaults de produção. Fixtures multissessão são evidência científica sintética. O ZIP
B3 de 10/09/2026 continua somente regressão técnica de um pregão e não é apresentado como
Walk-Forward/Monte Carlo/Sensitivity/Stress real ou OOS robusto.
