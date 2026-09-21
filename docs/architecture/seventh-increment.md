# Sétimo incremento: scoring absoluto e diversidade auditável

## Fluxo e fronteira

`Completed Research Evidence -> verified score inputs -> per-candidate absolute score -> raw
ranking -> semantic round-robin -> explicit Top N -> atomic scientific export`.

O pacote aditivo `quantlab-scoring` lê apenas nove artefatos de pesquisa necessários, verifica
hashes/IDs/fingerprints, revalida Candidate IDs e consistência Gate/status e rejeita fonte parcial,
corrompida ou com racional não canônico. Nenhum módulo de adapter, normalização, candle, feature,
backtest ou session-evaluation é importado/chamado. Logo o custo depende de candidatos e termos,
não do número de ticks.

## Independência e invalidação

O registro individual é uma função somente de evidência do próprio candidato + Score Policy.
Inserir/remover candidato não muda score, componentes ou `strategy_score_id` dos demais.

- Score Policy/evidência do candidato: recalcula score individual.
- Ranking Policy: preserva scores; refaz raw ranking e derivados.
- Diversity Policy: preserva score/raw; refaz diversified e Top N.
- Top N: preserva todos os anteriores; refaz somente Top N/export.
- rolling 19→20: CandidateSessionEvaluation pode ser reutilizada no nível de pesquisa, mas mudança
  nos agregados/comparison/gates invalida o score correspondente.

## Cache, retomada e publicação

O score completo é calculado deterministicamente para obter sua identidade; o cache é consultado
por `strategy_score_id`, validado por conteúdo e gravado em transação SQLite. Interrupção controlada
de aceite deixa somente entradas completas. Na retomada, scores existentes são reutilizados e o
ranking/export são sempre reconstruídos em ordem canônica. Nenhum diretório por candidato.

O destino não pode existir. Arquivos são construídos em staging no mesmo volume, hashes são
calculados e `os.replace` publica o diretório completo. Tempos e hits ficam no sidecar operacional,
fora de todos os fingerprints. Clean, warm e resume devem ser byte a byte idênticos.

## Escala e riscos

O motor realiza O(C*T) operações racionais para C candidatos e T=16 termos fixos, raw ranking
O(S log S) para S scores e diversidade O(S). A memória é O(C), limitada pelo Candidate Budget de
50 mil. O score não é robustez, portfólio, correlação, walk-forward, Monte Carlo, sensibilidade,
stress, ML/LLM ranking ou aprovação de trading real.
