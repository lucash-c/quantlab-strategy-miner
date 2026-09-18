# Sexto incremento: Discovery + Validation cronológicos

Este incremento adiciona um único holdout de TradingSessions ao Candidate Generator v1.
Não adiciona score, ranking, seleção de melhores candidatos, interpretação automática de
robustez, multi-timeframe dentro da mesma estratégia ou qualquer estatística avançada.
Cada candidato continua usando exatamente um timeframe.

## Fluxo e responsabilidades

```text
Histórico preparado: seleção de metadata, sem payload
  -> preflight bounded: CandidatePlan v1 inalterado
  -> ResearchSplitPlan + políticas explícitas
  -> ResearchProtocol imutável (antes dos resultados)
  -> Discovery: CandidateSessionEvaluation -> ledger cronológico -> PartitionEvaluation
  -> todos os Discovery Gates -> Discovery Pass Set
  -> Discovery Freeze: publicação atômica + validação de hashes
  -> Validation: SOMENTE candidatos aprovados
  -> PartitionEvaluation + comparação exata + todos os Validation Gates
  -> ValidationExperiment + publicação científica atômica
```

`quantlab-research` contém contratos, capacidade de acesso, split, gates, comparação, cache
de avaliação por sessão, freeze, orquestrador e export. A reconstrução de métricas a partir
dos ledgers está no módulo aditivo `quantlab_backtest.partition_metrics`. O módulo aditivo
`quantlab_data.session_catalog` separa seleção de metadata da resolução lazy de payload.
Não há mudança na matemática, schemas, candidate IDs ou engines quantitativos dos incrementos 1–5.

## Split e autorização

A entrada é um `market-historical-dataset-manifest/v1` preparado, com caches locais íntegros.
O planejamento lê esse manifest, mas não verifica bytes de ticks/candles/features, nem abre
CandidateSessionEvaluations de Validation. Ingestão anterior e existência física de caches
não são, isoladamente, utilização avaliativa de Validation.

A janela é a dos últimos `max_sessions` disponíveis, 19 por padrão. O split é metadata-only,
whole-session, disjunto, exaustivo e cronológico. A política inicial 13/6 é configurável,
não limite estrutural. Insuficiência é `INSUFFICIENT_SESSIONS`; não encurtar holdout, preencher
sessões ou retornar para in-sample. Sem calendário/downloader/rollover automático.

`ResearchAccess.check` precede toda resolução/avaliação de payload no orquestrador. Antes da
validação do freeze, acesso Validation é proibido para ticks, candles, features e avaliações
por sessão, mesmo se já estiverem em cache. Depois, somente IDs do pass set são autorizados.
Observers permitem provar a fronteira e contar os acessos. Pass set vazio é válido: zero
leitura de Validation e finalização formal `COMPLETE_WITHOUT_VALIDATION`.

## Protocolo, identidades e provenance

IDs são SHA-256 de JSON canônico com domínio explícito. Inteiros quantitativos são exatos;
frações são CanonicalRational reduzidas, denominador positivo, zero exclusivamente 0/1.
Paths, máquina, tempos, contadores de cache, SQLite e packing nunca são ciência.

| Identidade | Dependências normativas |
| --- | --- |
| `split_plan_id` | Histórico selecionado, política, ordem de sessões, ambos os bounds/views |
| `discovery_partition_id` / `validation_partition_id` | Somente sessões/datas do próprio view, ativo e políticas de sessão/tempo/candles |
| Gate policy ID | Schema, semântica AND, critérios canônicos ordenados/deduplicados |
| `criterion_id` | Métrica registrada, operador e threshold racional canônico |
| `research_protocol_id` | Search/policy/set/split IDs, AMBOS os gates, custos/slippage, engines/math/aggregation/comparison |
| `candidate_session_evaluation_id` | Candidato + sessão/contrato + próprios trades/candles/features + fricções + escala + engines/math/session policy |
| Session result fingerprint | Proveniência quantitativa, métricas, ledger e journal normalizados; independente dos packs/SQLite |
| `partition_evaluation_id` | Candidato, view, referências/fingerprints de avaliações por sessão ordenadas, aggregation policy |
| Partition result fingerprint | Registro quantitativo, métricas por sessão/globais, hashes do ledger/journal reconstruídos |
| `discovery_pass_set_id` | Próprio Discovery view/gate e membros aprovados com fingerprints de avaliação/gate Discovery |
| `discovery_freeze_id` | Protocolo e evidência Discovery persistida, hashes de resultados/gates/pass set |
| `validation_experiment_id` | Protocolo, freeze, próprio Validation view, resultados/comparações/gates/status finais |
| `export_id` | Manifest de bytes exportados + versões de export/Parquet; pode mudar com provenance |

O pass set não depende de Validation ou do Split Plan completo. `candidate_id` permanece o
do Generator v1: fricções afetam a avaliação, não o candidato. Papel DISCOVERY/VALIDATION,
split, gates e janela NÃO entram no ID do cache candidato/sessão.

`previous_experiment_id`, `previous_validation_partition_id` e overlap de holdout são somente
provenance. Não alteram avaliações, métricas, candidate IDs ou experiment ID quando a ciência
não muda. Não existe rastreamento global de exposição: `NOT_GLOBALLY_TRACKED`.
No rolling, 15…19 já podem ter sido holdout anterior; não chamar 15…20 de globalmente unseen.

## Cache, transações e memória

O cache central usa um SQLite operacional single-writer, adquirido ANTES de inicializar o
índice, e packs JSONL imutáveis/content-addressed. Uma transação por candidato/sessão grava
ledger, journal, métricas e fingerprint completo; falha causa rollback apenas dessa avaliação.
Resultados prontos sobrevivem interrupção e são publicados/validados antes de reutilização.

Packs agregam até 64 avaliações, normalmente publicados por sessão, sem um diretório/arquivo
por candidato. Hashes do pack detectam alteração física; fingerprint lógico detecta divergência
do índice/audit. O packing não define a identidade científica. Ausência/corrupção falha
explicitamente; não há recomputação silenciosa de evidência corrompida.

A execução mantém somente uma sessão de mercado/features por vez e itera candidatos/ledgers.
As features são a união das dependências dos candidatos autorizados daquele estágio; cada
cache key usa somente as próprias dependências do candidato. Sem carregar ticks da janela
histórica inteira. Limites do Generator v1 continuam obrigatórios. Metadados/results pequenos
de todos os candidatos são retidos na finalização; o budget é importante também para memória.

Rolling 1…19 -> 2…20 reutiliza qualquer avaliação compatível, mesmo após mudança de papel.
Com cobertura completa prévia, somente 20 é novo. Sem cobertura, candidatos barrados antes
podem exigir cálculo da sessão 14 agora Discovery. A sessão 1 é retirada da janela, NÃO do cache.

## Agregação e publicação

Ledgers por sessão ordenados por trading_date são a fonte normativa. Todos os campos `_units`
convertem multiplicativamente para escala comum exata; journal e ledger recebem ordinais
globais canônicos. Recalcular Metrics v3 do ledger global, nunca somar drawdowns ou fazer
médias de métricas de sessões. Drawdown é equity realizada, início zero. Sessão vazia não
interrompe streak; trade breakeven reinicia conforme Metrics v3.

Cada sessão tem linha de métricas, inclusive zero trades. Frações de contagem e concentração
incluem todas as sessões nos denominadores aprovados. Ver [contrato](../contracts/research-v1.md).

O checkpoint fica vinculado ao protocolo. Mudança normativa exige checkpoint/experimento
novo. Resume valida contexto, agregados/audit Discovery e freeze; Validation checkpoint só é
lido avaliativamente depois da liberação. Freeze ausente após haver refs Validation é erro;
freeze alterado jamais libera holdout. Gates/comparações são reconstruídos deterministicamente.

Export final é reconstruído em staging e publicado por rename atômico para destino inexistente.
Ordem científica: candidate_id, trading_date, ordinais, jamais performance ou ordem SQLite.
Parquet agregado usa `candidate_id` e JSON canônico exato em `record_json`; não converter
racionais para colunas float. Audit JSONL é shardado a cada 10.000 registros.
`validation-manifest.json` contém hashes de TODOS os artefatos; SQLite não é export científico.

Medições ficam em `<output>.operational.json`, fora do diretório científico. Built/reused e
tempos de evaluation, aggregation, gates, export, bytes de cache/artefatos não afetam IDs.
Tempos do relógio são operacionais, serializados como texto, não matemática do backtest.

## Aceites e limites

A suíte preserva os 105 testes anteriores e adiciona contratos, CLI, autorização, integridade,
cache/direct nos 4 timeframes com escala mista/custo/slippage, agregação, leakage, gates,
rolling com/sem cobertura e equivalência de todos os bytes clean/warm/resume.

`acceptance_research_fixture.py` produz evidência persistente de 20 sessões sintéticas e
quatro timeframes independentes; thresholds são exclusivamente fixtures.
`acceptance_research_real.py` compara seis candidatos da sessão WINV26 de 10/09/2026 ao
ledger/journal/metrics do quinto incremento e testa cache/determinismo. NÃO é OOS real 13/6.
Aceite OOS real permanece pendente dos arquivos correspondentes. ZIP grande não vai ao Git/CI.

Schemas novos são aditivos. Sem Score/ranking, walk-forward, Monte Carlo, otimização, LLM,
UI, APIs de corretora, ordens reais ou qualquer expansão do escopo aprovado.
