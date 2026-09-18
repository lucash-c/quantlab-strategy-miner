# Sexto incremento — evidência de aceite

Execuções locais finais em 18/09/2026, no repositório oficial, sem descartar o worktree
existente. O incremento adiciona holdout cronológico, gates formais, freeze, avaliações
candidato/sessão reutilizáveis, agregação e comparação exatas, CLI e exportação auditável.
Não há Strategy Score, ranking ou expansão além do escopo aprovado.

O commit final da main e o link exato de seu GitHub Actions são apresentados no relatório
de entrega da tarefa, após a confirmação remota, evitando referência recursiva deste arquivo
ao próprio hash. [Workflow CI da main](https://github.com/lucash-c/quantlab-strategy-miner/actions/workflows/ci.yml?query=branch%3Amain).

## Verificação dos 36 itens solicitados

| # | Item | Evidência |
| --- | --- | --- |
| 1 | Commit final/main | Hash exato na entrega; deve coincidir com origin/main e headSha do CI; worktree limpo. |
| 2 | Testes | 136 aprovados em283,92s:105 regressões preservadas +31 novos; rodada anterior135/135 também aprovada. |
| 3 | Ruff | `ruff check .`: All checks passed. |
| 4 | GitHub Actions | Verificação remota obrigatória antes da conclusão; link/headSha exatos na entrega. |
| 5 | ResearchSplitPlan | Modelo estrito + schema aditivo; metadata-only, sessões inteiras, ordenadas, disjuntas/exaustivas, IDs revalidados. |
| 6 | 13/6 | Discovery 03–19/08/2026 (13 sessões sintéticas); Validation 20–27/08/2026 (6); quatro TF independentes. |
| 7 | Insuficiência | INSUFFICIENT_SESSIONS em 1/6/18 sessões; CLI18 erro2 e zero resolução de payload; política configurável, sem redução automática. |
| 8–14 | IDs científicos | Tabela completa abaixo; mesmos IDs entre clean/warm/resume. |
| 15 | Pré-freeze | Ticks0, candles0, features0, SessionEvaluation0 em Validation; spies e capability checks. |
| 16 | A–F | A4 passam D+V; B12 passam D/falham V; C32 falham D, zero acesso V; D16 zero trades; E32 undefined gate failures; F8 concentrados. |
| 17 | Discovery Gate | Fixture explícita AND: trades GTE1, net_pnl GTE0; 16PASS/32FAIL. Sem defaults de performance. |
| 18 | Validation Gate | Mesma fixture explícita, congelada antes de D: 4PASS/12FAIL entre16 aprovados; demais32 NOT_RUN. |
| 19 | Sessões | Toda sessão tem linha, inclusive zero; 13 linhas por candidato D, 6 por candidato autorizado V. |
| 20 | Concentração | Exemplo F: uma sessão ativa/profitable,12 flat; lucro55 pontos; ambas as shares1/1; net/session55/13. |
| 21 | Comparação | 10 deltas V−D e 2 ratios V/D, sem interpretação/score; exemplo A: trades_delta−21, trades/session_ratio1/1. |
| 22 | Undefined | NO_TRADES/NO_WINS/NO_LOSSES/NO_PROFITABLE_SESSIONS; fontes comparativas preservadas; denominador D≤0 explicitamente indefinido. |
| 23 | Session cache | ID independente de papel/split/janela/gates; result fingerprint lógico + packs imutáveis, SQLite single-writer/transações. |
| 24 | Built/reused | Clean D624/0,V96/0; warm/resume D0/624,V0/96; rolling coberto D0/624,V48/240. |
| 25 | Cache/direct | Igualdade integral ledger/journal/metrics nos4 TF, escalas3/4, custo0.125/slippage0.25; engine mock prova hit sem backtest. |
| 26 | Drawdown | Oracle equity: DD13, soma de DD locais14; fonte normativa ledger cronológico, não soma/average de métricas. |
| 27 | Losing streak | −5, sessão vazia, −8 -> streak2; breakeven reinicia sequência conforme Metrics v3. |
| 28 | Rolling | 1…19 ->2…20; D2…14,V15…20; sessão14 troca papel e reutiliza ID; cache1 preservado; overlap conhecido5. |
| 29 | Clean/warm/resume | TODOS os bytes de31 artefatos finais iguais; interrupção controlada em V após3 conclusões, transações anteriores preservadas. |
| 30 | Alterar só V | Mesmo CandidateSet/IDs, D views/evaluations/metrics/gates/pass set; V/experiment podem mudar. Teste dedicado aprovado. |
| 31 | Alterar D | Regime Discovery alterado muda membros do pass set e acesso autorizado V. Teste dedicado aprovado. |
| 32 | B3 uma sessão | ZIP real/WINV26,6.262.194 trades,563 candles1m;6 avaliações novas e depois6 hits; ledger/journal/metrics idênticos ao quinto. NÃO OOS. |
| 33 | Regressão1–5 | Nenhuma alteração nos core/mining/engines antigos;105 casos continuam na suíte, CandidateGenerator160 e IDs preservados. |
| 34 | Fingerprints | JSONs de evidência completos abaixo, incluindo CSE IDs/result fingerprints, agregados, source e artifacts. |
| 35 | Benchmark | Tabela operacional abaixo; tempos/cache/paths/packing excluídos dos IDs científicos. |
| 36 | Commits | Oito commits de implementação/testes/docs + commit de evidências; lista abaixo e hash final na entrega. |

## Identidades da fixture de quatro timeframes

| Identidade | Valor |
| --- | --- |
| split_plan_id | sha256:4420fc9014915b468ddd26915b6e1f691e328fd3eb2d41ecdf75445447c4f9aa |
| discovery_partition_id | sha256:5adfedcf645b71f3680d28e4439fc218f3f1a7ddbdb994aed4c48bf68484c678 |
| validation_partition_id | sha256:0640d7262b4690709e51cb549fd2c052299c46301baf4299bce40920f5fea606 |
| research_protocol_id | sha256:21c9901e706d938c8f1d3aec2f7405db1d026aa141b4aa706c4fc1880385e765 |
| discovery_freeze_id | sha256:42c273b130076d4ca8af3c0b7ad728abf52391bf2eca17283ce0a03c9ce16ad2 |
| discovery_pass_set_id | sha256:959e0ff6e9727fff68727afdfee2bf8a68b7520f63eff4bab4f93e9bcdb6711f |
| validation_experiment_id | sha256:556b3529ac3c76bfc4c14a04ecbfe270d6dc92dbdbaaf5b659c6ff8ff2a93c8b |

Exemplo A: candidate40066b6c…; D39 trades/195 pontos/13sessões, V18trades/90pontos/6sessões.
Average trade5, trades/session3, net/session15 e active rate1/1 nos dois views; raw trades_delta−21
e active_sessions_delta−7 refletem contagens das partições de tamanhos diferentes, sem score.
Profit factor delta/ratio: null/SOURCE_METRIC_UNDEFINED, fontes NO_LOSSES em ambas as partições.

Exemplo F: candidate concentrado tem lucro55 pontos inteiramente na primeira sessão,1 ativa e
12 flat; shares lucro/trades1/1; active rate1/13; todas as13 sessões estão no denominador.
As métricas são fatos, não recomendação financeira nem interpretação de robustez.

## Freeze, autorização, integridade e pass set vazio

O observer do aceite conta zero acessos avaliativos Validation nos quatro tipos antes de
DISCOVERY_FREEZE_VALIDATED. O protocolo congela ambos os gates antes de qualquer resultado.
Freeze/pass set/evidência publicados por staging+rename atômico e validados antes da liberação.
Teste de alteração do arquivo de freeze falha antes de qualquer acesso V; ausência de freeze
após refs V também é erro. Mudança normativa no resume causa CHECKPOINT_CONTEXT_MISMATCH.

No caso E persistente, gate fixture profit_factor GTE1:48 candidatos avaliados,48FAIL,
32 com UNDEFINED_METRIC/NO_LOSSES e16 com threshold não atendido; pass set vazio válido,
Validation backtested0. Pass set:sha256:a67fe0671dc9c3bdabe48fd6aeedee75dd5f09801a1fc74e62ff8e237b2865e0.
Critério:sha256:32e8e1264fab296bed2f1d984f98aac2c4e6be35e0edd735b14a39c65b550ce1.
Gate sem critérios explicitamente configurado é PASS/NO_CRITERIA_CONFIGURED, não falha.

Corrupt pack/index, ordinais inconsistentes ou fingerprints divergentes falham explicitamente.
Falha simulada após inserção parcial causa rollback do atual; single-writer precede mutação.
Export final é reconstruído do zero por IDs/cronologia e publicado atomicamente, não em ordem
SQLite. Os resultados locais já concluídos não foram revertidos/sobrescritos.

## Benchmark operacional, não ciência

Medição Windows desta máquina, com concorrência de testes/regressão: não é garantia ou projeção.
Evaluation soma tempos das chamadas candidato/sessão; aggregation/gates somam os dois estágios.
Preparação de mercado/features e demais overhead estão no total, não nesses quatro componentes.

| Caminho fixture48 | Built/reused D | Built/reused V | Evaluation | Aggregation | Gates | Export | Total |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Clean | 624/0 | 96/0 | 16,92s | 6,74s | 0,62s | 1,17s | 37,56s |
| Warm | 0/624 | 0/96 | 3,01s | 4,71s | 0,54s | 1,11s | 16,93s |
| Resume final | 0/624 | 0/96 | 2,92s | 4,71s | 0,48s | 1,05s | 16,35s |
| Rolling, cobertura completa | 0/624 | 48/240 | 4,16s | 5,03s | 0,69s | 1,13s | 20,88s |

Interrupção/resume usa cache aquecido já existente, com3 solicitações V concluídas antes da
interrupção; retomada conta novamente requests compatíveis como hits, não3 novas avaliações.
Operacional interrompido consta no JSON. Clean produz720 avaliações distintas; warm/resume720hits.
Protocolo de cobertura explícita sem gates criou192 avaliações antes barradas no holdout;
somente depois seu rolling comprova48 novas (sessão20) e864 reusadas.
Teste separado sem cobertura comprova que candidatos anteriormente barrados exigem avaliações
novas da sessão14 agora Discovery: NÃO generalizar “apenas20 sempre é calculada”.

Fixture clean: cache70.041.929 bytes; export29.863.332 bytes em31 arquivos/shards.
Rolling: cache86.342.727 bytes; export32.495.131 bytes. Esses valores incluem índice operacional
e packs; não entram em fingerprints. Pico RAM não foi medido de forma confiável.

## Regressão B3 real, cold novo e warm

ZIP fonte preservado SHA256:fbb243028b3af09f47fb0311f7ea29bd8d8128b218ab04f14b5227e7ea5c4b3f.
Sessão:sha256:ad8560db2ed83ea4b9b9da886a85ff795f5ee669211097d3da91d6e0efe14794,
WINV26,10/09/2026;6.262.194 ticks elegíveis e563 candles1m.
UTC12:03:00.560…21:31:27.232; local B3 UTC−03:09:03…18:31.

Seis candidate IDs inalterados,33 trades no total. Resultados líquidos em candidate_id crescente:

| Prefixo | Trades | Gross | Slippage | Costs | Net pontos |
| --- | --- | --- | --- | --- | --- |
| 07d8db999e57 | 5 | −295 | 10 | 10 | −315 |
| 15054470b8a5 | 5 | −500 | 10 | 10 | −520 |
| 369d14a5fefc | 1 | −100 | 2 | 2 | −104 |
| 6ed3b64ab531 | 12 | −585 | 24 | 24 | −633 |
| 8a845e31d4df | 0 | 0 | 0 | 0 | 0 |
| edac089fffcc | 10 | −590 | 20 | 20 | −630 |

Ledger, journal e Metrics v3 iguais ao export final do quinto incremento em TODOS os seis
candidatos. Cache de avaliações novo separado, sem apagar caches:6 builds/0hits,293,60s
(evaluation+agregação+export JSON do script); warm0builds/6hits,0,056s na mesma instância, com
packs já verificados. Total300,92s inclui preparação/hash/cache de mercado. Não apresentar
o warm como processamento fresco de ticks ou benchmark cold em outro processo.

Rechecagem separada em processo novo, cache existente:8,04s total (6hits+6hits); mesmo hash.
Mercado/features reutilizados e íntegros; backtests novos percorrem os6.262.194 ticks para
cada candidato, inclusive zero-trade. Cache de avaliações novo1.237.903 bytes; artefatos846.712bytes.

SHA256 científico clean=warm=execução anterior:
f2152313418714be77c3146fa2ac0f54d17f0758ec3795ebe59059ea0a94b673.
Session/partition fingerprints individuais completos estão no JSON real.
Não houve divergência quantitativa nova. A documentação do perfil `_DRV` e TipoDoCanal opaco
permanece vigente. Este teste é uma sessão de regressão, NÃO aceite OOS real13/6.
Aceite OOS real continua pendente dos pregões correspondentes; não fabricar dados reais.

## Evidências e reprodução

[Fixture, IDs, métricas, hashes e benchmark](sixth-increment-fixture.json).
[B3 real, seis avaliações/fingerprints, métricas e benchmark](sixth-increment-real.json).
[Arquitetura](../architecture/sixth-increment.md), [contrato](../contracts/research-v1.md).

```powershell
uv sync --all-packages --locked
uv run ruff check .
uv run python scripts/run_tests.py
uv run python scripts/acceptance_research_fixture.py --work artifacts/research-fixture-new
uv run python scripts/acceptance_research_real.py --input C:/caminho/10-09-2026_NEGOCIOSAVISTA_DRV.zip --cache artifacts/fifth-real-20260916/cache --session-cache artifacts/research-real-new-cache --baseline artifacts/fifth-real-20260916/clean-final --output artifacts/research-real-new
```

ZIP completo não versionado; testes rápidos usam fixtures pequenas/offline. Cache e destinos
do aceite são novos para evitar apagar/sobrescrever trabalho existente.

## Commits do sexto incremento

- aee9ddb — contratos estritos, split cronológico e gates exatos.
- 3d8bbe4 — cache candidato/sessão e agregação de ledgers.
- 11a15d8 — autorização, freeze atômico, orquestração OOS e testes de leakage/integridade.
- 3fad70b — rejeitar freeze danificado/ausente no resume.
- 3e907da — CLI metadata-only, schemas aditivos e testes.
- 8ec6211 — rolling com candidatos anteriormente barrados.
- a1cc803 — aceites persistentes de fixtures e B3 real.
- ee2b986 — documentação normativa e utilização.

O commit final destas evidências e seu CI constam no histórico publicado/entrega da tarefa.
