# Décimo Incremento — Mining Intent e AUTO_DISCOVERY v1

## Escopo congelado da Fase 1

Esta fase introduz contratos estritos para transformar uma intenção de mineração em dois
subespaços unidirecionais explícitos consumidos pelo Candidate Generator v1. Ela não executa
Research, Validation, Score, Robustness ou backtest e não acessa dados de mercado.

O fluxo estrutural é:

```text
MiningRequestV1
    + AutoDiscoveryProfileV1 (por hash completo)
    -> MiningConstraintsV1
    -> ResolvedSearchSpaceV1 (BUY/SELL independentes)
    -> Candidate Generator v1
    -> CandidateUniverseManifestV1
```

`CONSTRAINED` aceita constraints explícitos. `AUTO_DISCOVERY` exige o
`auto_discovery_profile_id` completo; não há profile default ou alias `latest`. O alias é apenas
um nome versionado e não participa da identidade do conteúdo.

## Resolução e identidades

O protocolo `auto-discovery-resolution-protocol/v1`:

- resolve somente eixos estruturais;
- produz um `MiningSearchSpaceV1` por lado habilitado;
- não usa sampling nem truncamento;
- une os candidate IDs dos subplanos em ordem canônica;
- reaproveita `candidate-semantic/v1` para cada Strategy;
- calcula `candidate_universe_id` com `candidate-set/v1` sobre IDs únicos ordenados;
- verifica budget e expansion limit novamente sobre a união global.

As identidades científicas implementadas são:

| Contrato | Domain separation |
|---|---|
| `MiningRequestV1` | `mining-request/v1` |
| `MiningConstraintsV1` | `mining-constraints/v1` |
| `AutoDiscoveryProfileV1` | `auto-discovery-profile/v1` |
| `ResolvedSearchSpaceV1` | `resolved-search-space/v1` |
| Protocolo de resolução | `resolution-protocol/v1` |
| Universo de candidatos | `candidate-set/v1` |

Paths, PIDs, host, duração, timestamps, workers, batch size, cache, checkpoints, retries e
progresso não pertencem aos contratos e não entram nos fingerprints.

## AUTO_DISCOVERY_BALANCED_V1

O conteúdo normativo está em
`examples/mining/auto-discovery/balanced-v1.json`. Ele congela:

- WIN; timeframes 1m, 2m, 5m e 15m;
- BUY e SELL independentes e habilitados;
- entrada em `[09:00,18:25)`;
- AND-only, sem sampling e com truncamento proibido;
- stops e targets 5, 10, 20 e 40 pontos em ambos os lados;
- a matriz TC1–TC4, TP1–TP4, VW1–VW4, BO1–BO4, MO1–MO3 e CA1–CA5;
- budget e expansion limit de 8.192;
- expectativa obrigatória `T=6144, R=0, V=6144, D=0, U=6144`.

A distribuição congelada é 3.072 candidatos por lado, 1.536 por timeframe e 1.024 por
família.

## AUTO_DISCOVERY_ACCEPTANCE_V1

O conteúdo normativo está em
`examples/mining/auto-discovery/acceptance-v1.json`. Ele cobre as seis famílias, quatro
timeframes e os dois lados com 48 candidatos. BUY usa stop 5/target 10; SELL usa stop
10/target 5. Essa assimetria existe exclusivamente para provar independência dos lados.

O profile congela budget e expansion limit de 64 e a expectativa obrigatória
`T=48, R=0, V=48, D=0, U=48`.

## Limites científicos e operacionais

Uma divergência entre a cardinalidade observada e a cardinalidade congelada falha fechada.
Exceder `T` ou `U` também falha; o budget nunca seleciona um subconjunto.

Quantidade de workers, batch size e frequência de checkpoint são decisões operacionais
posteriores. Elas não mudam o universo e não pertencem aos fingerprints científicos.

## Fronteiras preservadas

Esta implementação não modifica Strategy Definition v3, Feature Engine, canonicalização de
Strategy, Candidate Generator v1, Research, Validation, Score, Robustness, gates ou evidências
do Nono Incremento. OR, NOT, LLMs e descoberta adaptativa permanecem fora do v1.

## Fase 2 — resolução, plano composto e persistência

O `ProfileRegistry` lê profiles versionados e só permite seleção pelo
`auto_discovery_profile_id` completo. Alias não é seletor, não existe `latest` e não há fallback
para um profile default. No modo `CONSTRAINED`, os constraints inline são a definição explícita.
No modo `AUTO_DISCOVERY`, o profile é a definição e os lados/timeframes do Request funcionam
somente como allowlist: podem reduzir o profile, nunca ampliá-lo.

Cada subespaço BUY ou SELL é enviado, sem adaptação científica, ao `preflight` legado. O
`CompositeCandidatePlan` une as tabelas disk-backed por `candidate_id`, ordena os IDs e:

- rejeita o mesmo ID com payload canônico diferente;
- consolida payloads idênticos sem perder nenhuma proveniência;
- não cria produto cartesiano BUY × SELL;
- aplica `candidate_budget` e `expansion_limit` à união global sem truncar;
- mantém `candidate-semantic/v1` e `candidate-set/v1` inalterados.

O cache do plano é endereçado por `resolved_search_space_id`, publicado via staging + rename e
validado por inventário, hashes, IDs e payloads canônicos antes do reuso. Cache ausente é
construído; cache existente divergente/corrompido falha fechado e jamais é sobrescrito.

O bundle científico é endereçado pelo conteúdo dos cinco arquivos canônicos:

- `mining-request.json`;
- `mining-constraints.json`;
- `auto-discovery-profile.json` (`null` no modo `CONSTRAINED`);
- `resolved-search-space.json`;
- `candidate-universe-manifest.json`.

Checkpoint operacional registra a fase e os IDs de contexto. Uma retomada com contexto diferente
falha como `CHECKPOINT_CONTEXT_MISMATCH`. Interrupções após resolução são retomáveis; uma
publicação já concluída só é reutilizada após comparação byte a byte. Workers, batch size, paths e
tempos não aparecem nos artefatos científicos nem em seus fingerprints.

Os comandos aditivos são:

```text
resolve-auto-discovery --request ... --profile-registry ...
plan-auto-discovery --request ... --profile-registry ... --cache ... --checkpoint ... --output ...
```

Ambos são estritamente estruturais. Eles não carregam catálogo de mercado nem iniciam Research,
Validation, backtest, Score ou Robustness. `plan-mining` e `run-mining` permanecem compatíveis.
