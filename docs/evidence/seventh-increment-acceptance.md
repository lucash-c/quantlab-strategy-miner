# Sétimo incremento — evidência de aceite

Execuções locais finais em 21/09/2026 sobre a evidência cronológica Discovery/Validation do sexto
incremento. O score é pesquisa determinística, não aprovação de trading e não representa OOS real
de 19 pregões B3. Precisão numérica de quatro casas não é precisão estatística.

O commit final e o GitHub Actions exatos são apresentados na entrega após a main remota verde.
[Workflow CI da main](https://github.com/lucash-c/quantlab-strategy-miner/actions/workflows/ci.yml?query=branch%3Amain).

## Resultado

- 178 testes locais verdes: 136 preservados + 42 novos.
- Ruff: `All checks passed!`.
- Candidate Set: 48; SCORED 4; NOT_ELIGIBLE 44; NOT_SCORABLE 0.
- Top N explícito 5 emitiu 4, sem preenchimento artificial.
- Scores/ranks: 100.0000, 99.7000, 98.5000, 91.0556.
- Distribuição: quatro observações em `[90,100]`; mediana units exata 991000.
- Clean 48 builds/0 hits; warm 0/48; resume após interrupção 17: 31/17.
- Todos os bytes científicos clean/warm/resume são idênticos.
- Zero replay: scorer não importa/chama adapter, normalizador, candles, features, backtest ou
  CandidateSessionEvaluation. O ZIP B3 de 10/09/2026 permanece somente regressão não-OOS já
  registrada em `sixth-increment-real.json`.

## Políticas e identidades

| Item | Identidade |
| --- | --- |
| ResearchScorePolicyV1 | `sha256:43088e265da86e649d3b75cbbea2ff48e9130330bf0ab11e4eb982bb6eb0f559` |
| ResearchRankingPolicyV1 | `sha256:a1194d3a2b4d073c94e964cd2bc737cae5554f99102e20d4b396ba3aff9b277c` |
| ResearchDiversityPolicyV1 | `sha256:084e8af5ad8420c98a7b2ee5cf65828c03a631bae6aab6403b4c0669c782baf5` |
| score_input_id | `sha256:d8c73763e1e5161e90dcee01055583d48f7f10b36612fe17658a6dc64278f800` |
| score_set_id | `sha256:f1efb2e38164f2bd098647226ea838be639b69bd77aca15584b00544c56d8193` |
| raw_ranking_id | `sha256:25971e38449b124c7465bce59d71f66e0bc444c757a4d37c23f86a8c53d1b902` |
| diversified_ranking_id | `sha256:0725e6206726a060690fe460c58c8c21fcf36e2360f6df7b68b6eef35d521d2b` |
| top_n_output_id | `sha256:bcbab0516e87f83bb95d73fbc750c053d1d705a232368de53d0a997fe637b555` |
| score_export_id | `sha256:d6bd74f036bccb355afb1dc09519b0e99704dd1a2b42e022b2a1fc40f89449d4` |

Os quatro `strategy_score_id` oficiais, `semantic_group_id`, exatos pré-quantização,
arredondamentos, hashes dos artefatos e benchmark estão no JSON de evidência ao lado.

## Oracles e invalidação seletiva

A: máximo sem penalty =100/1=1.000.000 units. B: cada transformação no meio produz base50,
penalty5 e score45=450.000. C: pré-clamp−10 resulta0. D: pré-clamp101 resulta100. E: HALF_EVEN
leva0,5 unit a0 e1,5 a2. F: dois exatos diferentes com mesmos units ignoram o exato e seguem
somente desempates oficiais.

Testes provam: candidato adicional não muda IDs/scores/componentes existentes; Score Policy muda
score IDs; Ranking Policy preserva scores e muda ranking; Diversity Policy preserva scores/raw e
muda diversified; Top N muda apenas Top N/export. Rolling com fingerprint agregado diferente
recalcula score mesmo que CandidateSessionEvaluation seja reutilizável em sua camada.

## Diversidade

O oracle raw A1/A2/A3/B1 produz diversified A1/B1/A2; A3 permanece auditável com
`DIVERSITY_GROUP_LIMIT`. Empate artificial de first-member raw rank usa `semantic_group_id`.
A assinatura abstrai valores numéricos, mas preserva posição/papel, dimensões, quantidade de
parâmetros, operadores e topologia. Raw rank nunca é reatribuído.

## Benchmark operacional não normativo

| Candidatos | Score (s) | Ranking + diversidade (s) |
| ---: | ---: | ---: |
| 48 | 0,168 | 0,0018 |
| 160 | 0,556 | 0,0065 |
| 1.000 | 3,404 | 0,0490 |
| 10.000 | 34,013 | 0,4665 |

Sem requisito rígido de tempo. A medida confirma crescimento por candidatos/termos, sem relação
com volume de ticks. Tempos, cache, máquina, caminhos e duração estão fora dos IDs científicos.
