# Nono incremento — evidência de aceite real

Execução concluída em 30/09/2026 sobre 20 arquivos oficiais B3 `_DRV`, ativo lógico `WIN`,
contrato físico `WINV26`, entre 24/08/2026 e 21/09/2026. O universo permaneceu congelado em 160
candidatos: 40 por timeframe 1m/2m/5m/15m. Nenhum resultado parcial foi usado para remover
candidatos, recalibrar parâmetros, mudar thresholds ou reinterpretar gates.

O resultado científico possui três conclusões independentes, que não devem ser combinadas:

| Camada | Resultado |
| --- | --- |
| Legacy Robustness Gate | 160 `ROBUSTNESS_INSUFFICIENT_EVIDENCE`; o mínimo legado de três folds permaneceu inalterado. |
| Ninth Increment Technical Completeness | 160 `NINTH_INCREMENT_TECHNICAL_COMPLETE`; dois folds e as quatro famílias obrigatórias foram executados. |
| Quantitative Verdict | 160 `FAILED`; conjunto qualificado vazio e nenhum candidato aprovado. |

Technical Completeness significa que a evidência obrigatória existe e é avaliável. Não significa
aprovação quantitativa. O gate legado também não foi redefinido para transformar dois folds em
três. Os 160 candidatos falharam os nove critérios quantitativos congelados: três Walk-Forward,
dois Monte Carlo, dois Sensitivity e dois Stress. Não houve `INSUFFICIENT_EVIDENCE` no veredito
quantitativo V2; houve reprovação pelos thresholds previamente congelados.

## Stress V1 preservado e emenda V2

A história possui duas etapas explícitas:

- V1 original: custo absoluto 1 e slippage absoluto 1 eram idênticos ao baseline. O engine
  corretamente registrou `INVALID_STRESS_SCENARIO / NON_STRESS_SCENARIO`, zero cenários válidos
  e Technical Completeness incompleta. A árvore científica V1 permanece preservada.
- V2 emendada: o commit de precommit
  `ed9731efb29d6b7243cbb8bcaa75e10a98ad3d74`, anterior a qualquer resultado V2, congelou um único
  cenário com custo × 1,5 e slippage × 1,5. A policy
  `sha256:870366affa27ac2b344fa1432bd237ece61ac906fcdfa81d893e020a66f8db7c`
  produziu um cenário válido para cada um dos 160 candidatos. Todos os resultados foram aceitos
  sem ajuste posterior da policy.

A emenda invalidou somente Stress e seus descendentes. Walk-Forward, Ninth Increment
Walk-Forward, Monte Carlo e Sensitivity conservaram, candidato por candidato, os IDs científicos
da V1. Research e Score foram reutilizados; todos os IDs Stress mudaram. Candidate universe,
Strategies, dataset, hashes de fonte, folds, sampler/seed, cenários Sensitivity, Score, ranking,
diversidade, gates, thresholds, fórmulas e algoritmos permaneceram inalterados.

## Dados, folds e identidades

- Dataset: `sha256:6e3fc4f0f6359e3b01c8163a7b06390df66be2845396b517ec27362e55296262`.
- Window fingerprint:
  `sha256:5c06418d192be74f5798eaabd417a7ce1311a1f4f301217bfb114592cbf5e4e1`.
- Research: 13 sessões Discovery e 6 Validation, conforme split congelado.
- Ninth Walk-Forward: dois folds completos. Fold 1 usa Discovery 24/08–10/09 e Validation
  11/09–18/09; fold 2 usa Discovery 25/08–11/09 e Validation 14/09–21/09.
- Robustness candidate set:
  `sha256:5d7c5cddd5c4a12c397f96e5c7d1cd127a17e14fe3ac060edf570e49d251935d`.
- Robustness protocol:
  `sha256:e08f550e62a002264b5e627c19c7e0d6ce73a4007cee2f35faeb442f06e1e999`.
- Export V2:
  `sha256:2db23e25ecbb8e6e0687d673516fa69152db61e0775789f5582c6d64727917b4`.
- Acceptance report local:
  `bb40fe1a82cf9f3263d290b3fe877053a901d2bc210a9afe4975b583e510e2e0`.

Os 20 hashes de origem estão no
[manifesto oficial congelado](ninth-increment-official-sources-v1.json). IDs adicionais, folds,
policies, contagens de critérios e hashes de evidência estão no
[JSON final](ninth-increment-real.json).

## Determinismo, resume e integridade

As execuções V2 clean, warm e resumed produziram 43 artefatos científicos byte-identical por
execução. Os manifests e todos os hashes internos foram revalidados. O checkpoint final contém
960 unidades completas: 160 para cada uma de Walk-Forward, Ninth Increment Walk-Forward, Monte
Carlo, Sensitivity, Stress e Quantitative Evidence.

Houve uma interrupção operacional recuperável depois de 84 unidades V2. O resume reutilizou o
mesmo checkpoint, cache e inputs científicos; não houve segunda execução concorrente nem mudança
de ciência. Registros de PID, tempos, cache e monitoramento permanecem operacionais e excluídos
dos fingerprints científicos.

## Limitação científica

Dois folds fornecem evidência temporal mais limitada que três ou mais. O aceite demonstra ingestão
multissessão oficial, execução OOS cronológica, ausência de leakage/reotimização, execução das
quatro famílias e aplicação dos critérios congelados. Não demonstra generalização futura nem
autoriza trading ao vivo. O resultado quantitativo observado é inequivocamente negativo: nenhum
candidato foi qualificado.

A validação local final concluiu 227 testes, zero falhas, e Ruff sem violações. O commit final e o
CI remoto são registrados na entrega somente depois de a `main` publicada estar verde.
